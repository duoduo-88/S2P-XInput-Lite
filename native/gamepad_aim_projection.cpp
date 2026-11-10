#include <algorithm>
#include <cmath>

namespace {

struct Point3 {
    double x, y, z;
};

struct Point2 {
    double x, y;
};

bool clip_near(Point3& start, Point3& end, double plane) {
    if (start.z < plane && end.z < plane) return false;
    if (start.z >= plane && end.z >= plane) return true;
    if (start.z >= plane) std::swap(start, end);
    const double amount = (plane - start.z) / (end.z - start.z);
    start = {
        start.x + (end.x - start.x) * amount,
        start.y + (end.y - start.y) * amount,
        plane,
    };
    return true;
}

bool clip_viewport(Point2 start, Point2 end, double width, double height, double* output) {
    if ((start.x < 0 && end.x < 0) || (start.x > width && end.x > width)
        || (start.y < 0 && end.y < 0) || (start.y > height && end.y > height)) {
        return false;
    }
    const double dx = end.x - start.x, dy = end.y - start.y;
    const double deltas[] = {-dx, dx, -dy, dy};
    const double distances[] = {start.x, width - start.x, start.y, height - start.y};
    double first = 0, last = 1;
    for (unsigned i = 0; i < 4; ++i) {
        if (deltas[i] < 0) first = std::max(first, distances[i] / deltas[i]);
        if (deltas[i] > 0) last = std::min(last, distances[i] / deltas[i]);
    }
    if (first > last) return false;
    output[0] = start.x + first * dx;
    output[1] = start.y + first * dy;
    output[2] = start.x + last * dx;
    output[3] = start.y + last * dy;
    return true;
}

} // namespace

extern "C" __declspec(dllexport) unsigned aim_project_room(
    const double* lines, unsigned count,
    double width, double height, double yaw, double pitch,
    double focal_scale, double near_plane, double* coordinates, unsigned* indices
) {
    const double cos_yaw = std::cos(yaw), sin_yaw = std::sin(yaw);
    const double cos_pitch = std::cos(pitch), sin_pitch = std::sin(pitch);
    const double cx = width / 2, cy = height / 2, focal = height * focal_scale;
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
        return {cx + focal * point.x / point.z, cy - focal * point.y / point.z};
    };
    unsigned visible = 0;
    for (unsigned i = 0; i < count; ++i) {
        const double* line = lines + 6 * i;
        Point3 start = transform(line), end = transform(line + 3);
        if (clip_near(start, end, near_plane)
            && clip_viewport(project(start), project(end), width, height, coordinates + 4 * visible)) {
            indices[visible++] = i;
        }
    }
    return visible;
}
