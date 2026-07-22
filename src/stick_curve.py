"""Driver-independent stick response-curve processing."""


def apply_stick_curve(value, curve_points, interpolation="LINEAR"):
    """Apply a signed 0..1 response curve using linear or monotone smoothing."""
    sign = -1.0 if value < 0.0 else 1.0
    magnitude = max(0.0, min(1.0, abs(value)))

    if interpolation.strip().upper() == "SMOOTH":
        knots = list(curve_points)
        if knots[0][0] > 1e-9:
            knots.insert(0, (0.0, 0.0))
        if knots[-1][0] < 1.0 - 1e-9:
            knots.append((1.0, 1.0))

        xs = [point[0] for point in knots]
        ys = [point[1] for point in knots]
        widths = [xs[index + 1] - xs[index] for index in range(len(xs) - 1)]

        # Invalid or vertical segments retain the predictable linear behavior.
        if widths and all(width > 1e-9 for width in widths):
            secants = [
                (ys[index + 1] - ys[index]) / widths[index]
                for index in range(len(widths))
            ]
            tangents = [0.0] * len(knots)
            tangents[0] = max(0.0, secants[0])
            tangents[-1] = max(0.0, secants[-1])

            for index in range(1, len(knots) - 1):
                left = secants[index - 1]
                right = secants[index]
                if left <= 0.0 or right <= 0.0:
                    tangents[index] = 0.0
                else:
                    weight_left = 2.0 * widths[index] + widths[index - 1]
                    weight_right = widths[index] + 2.0 * widths[index - 1]
                    tangents[index] = (weight_left + weight_right) / (
                        weight_left / left + weight_right / right
                    )

            for index, width in enumerate(widths):
                if xs[index] <= magnitude <= xs[index + 1]:
                    position = (magnitude - xs[index]) / width
                    squared = position * position
                    cubed = squared * position
                    output = (
                        (2 * cubed - 3 * squared + 1) * ys[index]
                        + (cubed - 2 * squared + position)
                        * width * tangents[index]
                        + (-2 * cubed + 3 * squared) * ys[index + 1]
                        + (cubed - squared) * width * tangents[index + 1]
                    )
                    return sign * max(0.0, min(1.0, output))

    first_x, first_y = curve_points[0]
    if magnitude <= first_x:
        output = first_y * magnitude / first_x if first_x > 1e-9 else first_y
        return sign * max(0.0, min(1.0, output))

    for index in range(len(curve_points) - 1):
        x1, y1 = curve_points[index]
        x2, y2 = curve_points[index + 1]
        if x1 <= magnitude <= x2:
            if abs(x2 - x1) < 1e-9:
                output = y2
            else:
                position = (magnitude - x1) / (x2 - x1)
                output = y1 + (y2 - y1) * position
            return sign * max(0.0, min(1.0, output))

    last_x, last_y = curve_points[-1]
    if last_x < 1.0 - 1e-9:
        position = (magnitude - last_x) / (1.0 - last_x)
        output = last_y + (1.0 - last_y) * position
    else:
        output = last_y
    return sign * max(0.0, min(1.0, output))
