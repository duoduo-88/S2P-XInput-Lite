#include <algorithm>
#include <cmath>

namespace {

struct Point3 {
    double x, y, z;
};

struct Point2 {
    float x, y;
};

struct Plane {
    double x, y, z, offset;

    double distance(Point3 point) const {
        return x * point.x + y * point.y + z * point.z + offset;
    }
};

Point3 intersect(Point3 outside, Point3 inside, double outside_distance, double inside_distance) {
    // Use the same direction on either side of a shared edge.
    const double amount = outside_distance / (outside_distance - inside_distance);
    return {
        outside.x + (inside.x - outside.x) * amount,
        outside.y + (inside.y - outside.y) * amount,
        outside.z + (inside.z - outside.z) * amount,
    };
}

unsigned clip_polygon(const Point3* input, unsigned count, const Plane& plane, Point3* output) {
    unsigned written = 0;
    Point3 previous = input[count - 1];
    double previous_distance = plane.distance(previous);
    for (unsigned i = 0; i < count; ++i) {
        const Point3 point = input[i];
        const double distance = plane.distance(point);
        if (previous_distance < 0 && distance >= 0) {
            output[written++] = intersect(previous, point, previous_distance, distance);
        } else if (previous_distance >= 0 && distance < 0) {
            output[written++] = intersect(point, previous, distance, previous_distance);
        }
        if (distance >= 0) output[written++] = point;
        previous = point;
        previous_distance = distance;
    }
    return written;
}

} // namespace

extern "C" __declspec(dllexport) unsigned aim_project_tiles(
    const double* quads, unsigned count,
    double width, double height, double yaw, double pitch,
    double focal_scale, double near_plane, float* triangles
) {
    const double cos_yaw = std::cos(yaw), sin_yaw = std::sin(yaw);
    const double cos_pitch = std::cos(pitch), sin_pitch = std::sin(pitch);
    const double cx = width / 2, cy = height / 2, focal = height * focal_scale;
    const Plane planes[] = {
        {0, 0, 1, -near_plane},
        {focal, 0, cx, 0},
        {-focal, 0, cx, 0},
        {0, focal, cy, 0},
        {0, -focal, cy, 0},
    };
    const auto transform = [&](const double* point) -> Point3 {
        const double x = point[0], y = point[1], z = point[2];
        const double forward = x * sin_yaw + z * cos_yaw;
        return {
            x * cos_yaw - z * sin_yaw,
            y * cos_pitch - forward * sin_pitch,
            y * sin_pitch + forward * cos_pitch,
        };
    };
    const auto project = [&](Point3 point) -> Point2 {
        return {
            static_cast<float>(std::clamp(cx + focal * point.x / point.z, 0.0, width)),
            static_cast<float>(std::clamp(cy - focal * point.y / point.z, 0.0, height)),
        };
    };
    unsigned written = 0;
    for (unsigned i = 0; i < count; ++i) {
        // A convex quad gains at most one vertex per clipping plane.
        const double* quad = quads + 12 * i;
        Point3 polygon[9] = {transform(quad), transform(quad + 3), transform(quad + 6), transform(quad + 9)};
        Point3 scratch[9];
        Point3* input = polygon;
        Point3* output = scratch;
        unsigned length = 4;
        for (const Plane& plane : planes) {
            length = clip_polygon(input, length, plane, output);
            std::swap(input, output);
            if (length < 3) break;
        }
        if (length < 3) continue;

        const Point2 first = project(input[0]);
        Point2 previous = project(input[1]);
        for (unsigned vertex = 2; vertex < length; ++vertex) {
            const Point2 point = project(input[vertex]);
            *triangles++ = first.x;
            *triangles++ = first.y;
            *triangles++ = previous.x;
            *triangles++ = previous.y;
            *triangles++ = point.x;
            *triangles++ = point.y;
            ++written;
            previous = point;
        }
    }
    return written;
}
