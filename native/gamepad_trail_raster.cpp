#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <utility>
#include <vector>

namespace {
struct TrailSample {
    double stamp;
    double x;
    double y;
    std::uint64_t sequence;
};

struct TrailStyle {
    int percent;
    double length;
    double center;
    double radius;
    double zoom;
    double pan_x;
    double pan_y;
};

struct TrailUpdate {
    int left;
    int top;
    int right;
    int bottom;
    double next_expiry;
};

using Expiry = std::pair<double, std::size_t>;
constexpr std::greater<Expiry> earliest_first{};
constexpr double dot_radius_squared = 1.15 * 1.15;

class Raster {
public:
    Raster(int width, int height, std::uint32_t color)
        : width_(width), height_(height), color_(color),
          pixels_(static_cast<std::size_t>(width) * height),
          expiry_(pixels_.size()), batch_expiry_(pixels_.size()) {}

    const std::uint32_t* pixels() const { return pixels_.data(); }

    void update(const TrailSample* samples, std::size_t count, const TrailStyle& style,
                double now, bool reset, TrailUpdate& result) {
        result = {width_, height_, 0, 0, std::numeric_limits<double>::infinity()};
        if (reset) {
            std::fill(pixels_.begin(), pixels_.end(), 0);
            std::fill(expiry_.begin(), expiry_.end(), 0.0);
            heap_.clear();
            live_pixels_ = 0;
            result.left = result.top = 0;
            result.right = width_;
            result.bottom = height_;
        }

        for (std::size_t i = 0; i < count; ++i) {
            const auto& sample = samples[i];
            if ((sample.sequence % 100) * style.percent % 100 >=
                static_cast<unsigned>(style.percent)) {
                continue;
            }
            const double expires = sample.stamp + style.length;
            if (expires <= now) {
                continue;
            }
            const double x = style.center + sample.x * style.radius * style.zoom + style.pan_x;
            const double y = style.center - sample.y * style.radius * style.zoom + style.pan_y;
            if (!std::isfinite(x) || !std::isfinite(y) ||
                x < -2 || x > width_ + 1 || y < -2 || y > height_ + 1) {
                continue;
            }
            const int center_x = static_cast<int>(std::floor(x));
            const int center_y = static_cast<int>(std::floor(y));
            for (int row = std::max(0, center_y - 1); row < std::min(height_, center_y + 2); ++row) {
                const double dy = row + 0.5 - y;
                for (int column = std::max(0, center_x - 1);
                     column < std::min(width_, center_x + 2); ++column) {
                    const double dx = column + 0.5 - x;
                    if (dx * dx + dy * dy > dot_radius_squared) {
                        continue;
                    }
                    const auto pixel = static_cast<std::size_t>(row) * width_ + column;
                    if (expires > batch_expiry_[pixel]) {
                        if (batch_expiry_[pixel] == 0) {
                            touched_.push_back(pixel);
                        }
                        batch_expiry_[pixel] = expires;
                    }
                }
            }
        }

        expire(now, result);
        for (const auto pixel : touched_) {
            const double expires = std::exchange(batch_expiry_[pixel], 0.0);
            if (expires <= expiry_[pixel]) {
                continue;
            }
            expiry_[pixel] = expires;
            heap_.emplace_back(expires, pixel);
            std::push_heap(heap_.begin(), heap_.end(), earliest_first);
            if (pixels_[pixel] == 0) {
                pixels_[pixel] = color_;
                ++live_pixels_;
                mark_dirty(pixel, result);
            }
        }
        touched_.clear();

        if (heap_.size() > std::max<std::size_t>(2048, live_pixels_ * 8)) {
            heap_.clear();
            for (std::size_t pixel = 0; pixel < expiry_.size(); ++pixel) {
                if (expiry_[pixel] > now) {
                    heap_.emplace_back(expiry_[pixel], pixel);
                }
            }
            std::make_heap(heap_.begin(), heap_.end(), earliest_first);
        }
        if (!heap_.empty()) {
            result.next_expiry = heap_.front().first;
        }
    }

private:
    void expire(double now, TrailUpdate& result) {
        while (!heap_.empty()) {
            const auto [expires, pixel] = heap_.front();
            if (expiry_[pixel] == expires) {
                if (expires > now) {
                    break;
                }
                expiry_[pixel] = 0;
                pixels_[pixel] = 0;
                --live_pixels_;
                mark_dirty(pixel, result);
            }
            std::pop_heap(heap_.begin(), heap_.end(), earliest_first);
            heap_.pop_back();
        }
    }

    void mark_dirty(std::size_t pixel, TrailUpdate& result) const {
        const int row = static_cast<int>(pixel / width_);
        const int column = static_cast<int>(pixel % width_);
        result.left = std::min(result.left, column);
        result.top = std::min(result.top, row);
        result.right = std::max(result.right, column + 1);
        result.bottom = std::max(result.bottom, row + 1);
    }

    int width_;
    int height_;
    std::uint32_t color_;
    std::size_t live_pixels_ = 0;
    std::vector<std::uint32_t> pixels_;
    std::vector<double> expiry_;
    std::vector<double> batch_expiry_;
    std::vector<std::size_t> touched_;
    std::vector<Expiry> heap_;
};
}  // namespace

extern "C" __declspec(dllexport) void* s2p_trail_create(int width, int height, std::uint32_t color) noexcept {
    if (width <= 0 || width > 32767 || height <= 0 || height > 32767 || color == 0) {
        return nullptr;
    }
    try {
        return new Raster(width, height, color);
    } catch (...) {
        return nullptr;
    }
}

extern "C" __declspec(dllexport) void s2p_trail_destroy(void* handle) noexcept {
    delete static_cast<Raster*>(handle);
}

extern "C" __declspec(dllexport) const std::uint32_t* s2p_trail_pixels(void* handle) noexcept {
    return static_cast<Raster*>(handle)->pixels();
}

extern "C" __declspec(dllexport) int s2p_trail_update(
    void* handle, const TrailSample* samples, std::size_t count, const TrailStyle* style,
    double now, int reset, TrailUpdate* result) noexcept {
    try {
        static_cast<Raster*>(handle)->update(samples, count, *style, now, reset != 0, *result);
        return 1;
    } catch (...) {
        return 0;
    }
}
