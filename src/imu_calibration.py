"""Numerically validated IMU calibration helpers.

The functions in this module are intentionally independent from the GUI and
controller transport so that calibration math can be tested with synthetic
sensor data before it is applied to a real controller.
"""

import math

import numpy as np


def _as_samples(samples, minimum=30):
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) < minimum:
        raise ValueError("insufficient_samples")
    if not np.all(np.isfinite(values)):
        raise ValueError("invalid_samples")
    return values


def apply_matrix_calibration(value, bias, matrix):
    """Return matrix @ (value - bias) as a three-value tuple."""
    vector = np.asarray(value, dtype=np.float64)
    offset = np.asarray(bias, dtype=np.float64)
    transform = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    corrected = transform @ (vector - offset)
    if not np.all(np.isfinite(corrected)):
        raise ValueError("invalid_calibrated_value")
    return tuple(float(item) for item in corrected)


def _fit_ellipsoid_once(samples):
    """Algebraically fit x.T Q x + 2 u.T x = 1."""
    origin = np.mean(samples, axis=0)
    axis_scale = np.std(samples, axis=0)
    if np.min(axis_scale) <= 1e-9:
        raise ValueError("insufficient_3d_coverage")
    normalized_samples = (samples - origin) / axis_scale
    x, y, z = normalized_samples.T
    design = np.column_stack((
        x * x,
        y * y,
        z * z,
        2.0 * x * y,
        2.0 * x * z,
        2.0 * y * z,
        2.0 * x,
        2.0 * y,
        2.0 * z,
    ))
    coefficients, _residuals, rank, _singular = np.linalg.lstsq(
        design, np.ones(len(samples)), rcond=None
    )
    if rank < 9:
        raise ValueError("insufficient_3d_coverage")
    q = np.array((
        (coefficients[0], coefficients[3], coefficients[4]),
        (coefficients[3], coefficients[1], coefficients[5]),
        (coefficients[4], coefficients[5], coefficients[2]),
    ))
    u = coefficients[6:9]
    q = (q + q.T) * 0.5
    eigenvalues = np.linalg.eigvalsh(q)
    if np.min(eigenvalues) <= 0.0:
        raise ValueError("non_ellipsoidal_samples")
    center_normalized = -np.linalg.solve(q, u)
    radius_term = 1.0 + float(center_normalized @ q @ center_normalized)
    if not math.isfinite(radius_term) or radius_term <= 0.0:
        raise ValueError("non_ellipsoidal_samples")
    normalized_q = q / radius_term
    eigenvalues, eigenvectors = np.linalg.eigh(normalized_q)
    if np.min(eigenvalues) <= 0.0:
        raise ValueError("non_ellipsoidal_samples")
    # A.T A = normalized_q, therefore ||A (x-center)|| == 1.
    center = origin + axis_scale * center_normalized
    inverse_scale = np.diag(1.0 / axis_scale)
    physical_shape = inverse_scale @ normalized_q @ inverse_scale
    physical_shape = (physical_shape + physical_shape.T) * 0.5
    physical_eigenvalues, physical_eigenvectors = np.linalg.eigh(
        physical_shape
    )
    if np.min(physical_eigenvalues) <= 0.0:
        raise ValueError("non_ellipsoidal_samples")
    # Use the unique symmetric positive-definite square root.  Other square
    # roots can also make a sphere but may rotate the sensor frame, which is
    # unacceptable when gyro, accel, and magnetometer axes must stay aligned.
    matrix = (
        physical_eigenvectors
        @ np.diag(np.sqrt(physical_eigenvalues))
        @ physical_eigenvectors.T
    )
    corrected = (samples - center) @ matrix.T
    norms = np.linalg.norm(corrected, axis=1)
    return center, matrix, norms, physical_eigenvalues


def fit_magnetometer_ellipsoid(samples):
    """Fit hard/soft-iron calibration with outlier rejection and quality data.

    Returns ``(bias, matrix, quality)``.  The matrix maps calibrated samples to
    an approximately unit sphere.  Poor 3D coverage, excessive distortion, or
    a large residual is rejected instead of producing unsafe calibration data.
    """
    values = _as_samples(samples, minimum=120)
    if len(values) > 6000:
        # Preserve the complete motion while bounding the least-squares cost.
        indices = np.linspace(0, len(values) - 1, 6000).astype(int)
        values = values[indices]

    filtered = values
    for _ in range(3):
        center, matrix, norms, eigenvalues = _fit_ellipsoid_once(filtered)
        residual = np.abs(norms - np.median(norms))
        median_residual = float(np.median(residual))
        threshold = max(0.025, 4.5 * 1.4826 * median_residual)
        keep = residual <= threshold
        if np.count_nonzero(keep) < max(120, int(len(filtered) * 0.70)):
            raise ValueError("excessive_magnetic_outliers")
        if np.all(keep):
            break
        filtered = filtered[keep]

    center, matrix, norms, eigenvalues = _fit_ellipsoid_once(filtered)
    rms = float(np.sqrt(np.mean((norms - 1.0) ** 2)))
    p95 = float(np.percentile(np.abs(norms - 1.0), 95))
    condition = float(np.max(eigenvalues) / np.min(eigenvalues))
    corrected = (filtered - center) @ matrix.T
    covariance_eigenvalues = np.linalg.eigvalsh(
        np.cov(corrected, rowvar=False)
    )
    coverage = float(
        np.min(covariance_eigenvalues) / np.max(covariance_eigenvalues)
    )

    if condition > 25.0:
        raise ValueError("excessive_soft_iron_distortion")
    if coverage < 0.08:
        raise ValueError("insufficient_3d_coverage")
    if rms > 0.12 or p95 > 0.25:
        raise ValueError("poor_ellipsoid_fit")
    if np.any(np.abs(center) > 32768.0):
        raise ValueError("invalid_hard_iron_bias")

    quality = {
        "sample_count": int(len(filtered)),
        "rejected_count": int(len(values) - len(filtered)),
        "rms_residual": rms,
        "p95_residual": p95,
        "condition": condition,
        "coverage": coverage,
    }
    return (
        tuple(float(item) for item in center),
        tuple(tuple(float(item) for item in row) for row in matrix),
        quality,
    )


def gyro_calibration_quality(samples):
    """Return gyro bias/noise metrics, rejecting motion or noisy captures."""
    values = _as_samples(samples, minimum=100)
    median = np.median(values, axis=0)
    distances = np.linalg.norm(values - median, axis=1)
    cutoff = max(8.0, float(np.percentile(distances, 95)))
    filtered = values[distances <= cutoff]
    if len(filtered) < int(len(values) * 0.85):
        raise ValueError("unstable")
    bias = np.mean(filtered, axis=0)
    stddev = np.std(filtered, axis=0)
    peak_to_peak = np.ptp(filtered, axis=0)
    if np.max(stddev) > 35.0 or np.max(peak_to_peak) > 180.0:
        raise ValueError("unstable")
    return (
        tuple(float(item) for item in bias),
        {
            "sample_count": int(len(filtered)),
            "stddev": tuple(float(item) for item in stddev),
            "peak_to_peak": tuple(float(item) for item in peak_to_peak),
        },
    )


def fit_accelerometer_ellipsoid(samples):
    """Fit static-gravity samples without assuming six exact housing faces."""
    bias, matrix, quality = fit_magnetometer_ellipsoid(samples)
    if quality["coverage"] < 0.25:
        raise ValueError("insufficient_accelerometer_coverage")
    if quality["condition"] > 2.0:
        raise ValueError("excessive_accelerometer_distortion")
    if quality["rms_residual"] > 0.045 or quality["p95_residual"] > 0.09:
        raise ValueError("poor_accelerometer_fit")
    matrix_array = np.asarray(matrix, dtype=np.float64)
    symmetry_error = float(np.max(np.abs(matrix_array - matrix_array.T)))
    if symmetry_error > 1e-10:
        raise ValueError("accelerometer_frame_rotation")
    quality = dict(quality)
    quality["symmetry_error"] = symmetry_error
    return bias, matrix, quality
