#include <cuda_runtime.h>
#include <math_constants.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <vector>

namespace {
constexpr std::uint32_t kAbiVersion = 2;
constexpr int kProbeElementCount = 4096;

#pragma pack(push, 1)
struct ProbeResult {
    std::uint32_t abi_version;
    std::int32_t status;
    std::int32_t device_count;
    std::int32_t selected_device;
    std::int32_t compute_major;
    std::int32_t compute_minor;
    std::int32_t multiprocessor_count;
    std::int32_t driver_version;
    std::int32_t runtime_version;
    std::uint64_t total_memory_bytes;
    std::uint64_t free_memory_bytes;
    float self_test_max_error;
    char device_name[256];
    char error[512];
};

struct ProjectionConfigInput {
    std::uint32_t abi_version;
    std::int32_t image_width;
    std::int32_t image_height;
    std::int32_t depth_width;
    std::int32_t depth_height;
    std::int32_t distortion_model;
    std::int32_t coefficient_count;
    double fx;
    double fy;
    double cx;
    double cy;
    double coefficients[12];
    double minimum_depth;
    double maximum_depth;
    double border_fraction;
    double minimum_luminance;
    double maximum_luminance;
    double maximum_distance;
    double occlusion_absolute;
    double occlusion_relative;
    std::int32_t reject_exposure_extremes;
    std::int32_t illumination_balancing;
};

struct ProjectionFrameInput {
    double center[3];
    double rotation[9];
    double sharpness_weight;
    std::uint16_t source_flight;
};

struct IlluminationInput {
    std::int32_t enabled;
    double radial;
    double exposure;
    double confidence;
    double radius_limit;
};
#pragma pack(pop)

struct DeviceConfig {
    int image_width;
    int image_height;
    int depth_width;
    int depth_height;
    int distortion_model;
    int coefficient_count;
    double fx;
    double fy;
    double cx;
    double cy;
    double coefficients[12];
    double minimum_depth;
    double maximum_depth;
    double border_fraction;
    double minimum_luminance;
    double maximum_luminance;
    double maximum_distance;
    double occlusion_absolute;
    double occlusion_relative;
    int reject_exposure_extremes;
    int illumination_balancing;
};

struct DeviceFrame {
    double center[3];
    double rotation[9];
    double sharpness_weight;
    std::uint16_t source_flight;
    std::uint64_t image_offset;
    int illumination_enabled;
    double radial;
    double exposure;
    double confidence;
    double radius_limit;
};

struct BatchContext {
    DeviceConfig host_config{};
    std::vector<DeviceFrame> host_frames;
    DeviceConfig* device_config = nullptr;
    DeviceFrame* device_frames = nullptr;
    std::uint8_t* device_images = nullptr;
    float* device_depth = nullptr;
    float* device_depth_temp = nullptr;
    double* device_xyz = nullptr;
    std::uint16_t* device_colors = nullptr;
    float* device_quality = nullptr;
    float* device_distances = nullptr;
    std::uint16_t* device_sources = nullptr;
    unsigned long long* device_counters = nullptr;
    std::size_t capacity = 0;
    int frame_count = 0;

    ~BatchContext() {
        cudaFree(device_counters);
        cudaFree(device_sources);
        cudaFree(device_distances);
        cudaFree(device_quality);
        cudaFree(device_colors);
        cudaFree(device_xyz);
        cudaFree(device_depth_temp);
        cudaFree(device_depth);
        cudaFree(device_images);
        cudaFree(device_frames);
        cudaFree(device_config);
    }
};

void write_error(char* destination, std::uint32_t length, const char* operation,
                 cudaError_t error) {
    if (destination != nullptr && length > 0) {
        std::snprintf(destination, length, "%s: %s", operation,
                      cudaGetErrorString(error));
    }
}

void write_message(char* destination, std::uint32_t length, const char* message) {
    if (destination != nullptr && length > 0) {
        std::snprintf(destination, length, "%s", message);
    }
}

__global__ void checkpoint_kernel(const float* input, float* output, int count) {
    const int index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < count) {
        output[index] = input[index] * 2.0f + 1.0f;
    }
}

__device__ bool project_point(const double* xyz, const DeviceFrame& frame,
                              const DeviceConfig& config, bool use_border,
                              double& camera_x, double& camera_y, double& camera_z,
                              double& u, double& v) {
    const double dx = xyz[0] - frame.center[0];
    const double dy = xyz[1] - frame.center[1];
    const double dz = xyz[2] - frame.center[2];
    camera_x = dx * frame.rotation[0] + dy * frame.rotation[3] + dz * frame.rotation[6];
    camera_y = dx * frame.rotation[1] + dy * frame.rotation[4] + dz * frame.rotation[7];
    camera_z = dx * frame.rotation[2] + dy * frame.rotation[5] + dz * frame.rotation[8];
    if (!isfinite(camera_x) || !isfinite(camera_y) || !isfinite(camera_z)
            || camera_z < config.minimum_depth || camera_z > config.maximum_depth) {
        return false;
    }

    const double x = camera_x / camera_z;
    const double y = camera_y / camera_z;
    double projected_x = x;
    double projected_y = y;
    if (config.distortion_model == 1) {
        const double r2 = x * x + y * y;
        const double r4 = r2 * r2;
        const double r6 = r4 * r2;
        const double k1 = config.coefficient_count > 0 ? config.coefficients[0] : 0.0;
        const double k2 = config.coefficient_count > 1 ? config.coefficients[1] : 0.0;
        const double p1 = config.coefficient_count > 2 ? config.coefficients[2] : 0.0;
        const double p2 = config.coefficient_count > 3 ? config.coefficients[3] : 0.0;
        const double k3 = config.coefficient_count > 4 ? config.coefficients[4] : 0.0;
        const double k4 = config.coefficient_count > 5 ? config.coefficients[5] : 0.0;
        const double k5 = config.coefficient_count > 6 ? config.coefficients[6] : 0.0;
        const double k6 = config.coefficient_count > 7 ? config.coefficients[7] : 0.0;
        const double s1 = config.coefficient_count > 8 ? config.coefficients[8] : 0.0;
        const double s2 = config.coefficient_count > 9 ? config.coefficients[9] : 0.0;
        const double s3 = config.coefficient_count > 10 ? config.coefficients[10] : 0.0;
        const double s4 = config.coefficient_count > 11 ? config.coefficients[11] : 0.0;
        const double radial = (1.0 + k1 * r2 + k2 * r4 + k3 * r6)
                            / (1.0 + k4 * r2 + k5 * r4 + k6 * r6);
        projected_x = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
                    + s1 * r2 + s2 * r4;
        projected_y = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
                    + s3 * r2 + s4 * r4;
    } else if (config.distortion_model == 2) {
        const double r = hypot(x, y);
        if (r > 1.0e-12) {
            const double theta = atan(r);
            const double theta2 = theta * theta;
            double polynomial = 1.0;
            double power = theta2;
            for (int index = 0; index < 4; ++index) {
                polynomial += config.coefficients[index] * power;
                power *= theta2;
            }
            const double scale = theta * polynomial / r;
            projected_x = x * scale;
            projected_y = y * scale;
        }
    }
    u = config.fx * projected_x + config.cx;
    v = config.fy * projected_y + config.cy;
    const double pad_x = use_border ? config.image_width * config.border_fraction : 0.0;
    const double pad_y = use_border ? config.image_height * config.border_fraction : 0.0;
    return isfinite(u) && isfinite(v)
        && u >= pad_x && v >= pad_y
        && u < static_cast<double>(config.image_width - 1) - pad_x
        && v < static_cast<double>(config.image_height - 1) - pad_y;
}

__device__ void atomic_min_positive(float* address, float value) {
    int* integer_address = reinterpret_cast<int*>(address);
    int old = *integer_address;
    while (value < __int_as_float(old)) {
        const int assumed = old;
        old = atomicCAS(integer_address, assumed, __float_as_int(value));
        if (old == assumed) {
            break;
        }
    }
}

__global__ void initialize_depth(float* depth, std::size_t count) {
    const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index < count) {
        depth[index] = CUDART_INF_F;
    }
}

__global__ void visibility_kernel(const double* xyz, std::size_t point_count,
                                  const DeviceFrame* frames, int frame_count,
                                  const DeviceConfig* config, float* depth) {
    const std::size_t point = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const int frame_index = blockIdx.y;
    if (point >= point_count || frame_index >= frame_count) {
        return;
    }
    double x, y, z, u, v;
    if (!project_point(xyz + point * 3, frames[frame_index], *config, false,
                       x, y, z, u, v)) {
        return;
    }
    const int depth_x = static_cast<int>(u * config->depth_width / config->image_width);
    const int depth_y = static_cast<int>(v * config->depth_height / config->image_height);
    if (depth_x >= 0 && depth_x < config->depth_width
            && depth_y >= 0 && depth_y < config->depth_height) {
        const std::size_t pixels = static_cast<std::size_t>(config->depth_width) * config->depth_height;
        atomic_min_positive(depth + static_cast<std::size_t>(frame_index) * pixels
                            + static_cast<std::size_t>(depth_y) * config->depth_width + depth_x,
                            static_cast<float>(z));
    }
}

__global__ void erode_depth_kernel(const float* input, float* output, int radius,
                                   int width, int height, int frame_count) {
    const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t pixels = static_cast<std::size_t>(width) * height;
    const std::size_t total = pixels * frame_count;
    if (index >= total) {
        return;
    }
    const int frame = static_cast<int>(index / pixels);
    const int local = static_cast<int>(index % pixels);
    const int center_y = local / width;
    const int center_x = local - center_y * width;
    float value = CUDART_INF_F;
    for (int dy = -radius; dy <= radius; ++dy) {
        const int y = center_y + dy;
        if (y < 0 || y >= height) continue;
        for (int dx = -radius; dx <= radius; ++dx) {
            const int x = center_x + dx;
            if (x < 0 || x >= width) continue;
            value = fminf(value, input[static_cast<std::size_t>(frame) * pixels
                                      + static_cast<std::size_t>(y) * width + x]);
        }
    }
    output[index] = value;
}

__device__ float srgb_to_linear(std::uint8_t value) {
    const float normalized = value / 255.0f;
    return normalized <= 0.04045f ? normalized / 12.92f
        : powf((normalized + 0.055f) / 1.055f, 2.4f);
}

__device__ std::uint8_t linear_to_srgb(float value) {
    value = fminf(1.0f, fmaxf(0.0f, value));
    const float encoded = value <= 0.0031308f ? value * 12.92f
        : 1.055f * powf(value, 1.0f / 2.4f) - 0.055f;
    return static_cast<std::uint8_t>(fminf(255.0f, fmaxf(0.0f, nearbyintf(255.0f * encoded))));
}

__device__ void sample_rgb(const std::uint8_t* images, const DeviceFrame& frame,
                           const DeviceConfig& config, double u, double v,
                           std::uint8_t rgb[3]) {
    const int x = static_cast<int>(floor(u));
    const int y = static_cast<int>(floor(v));
    const double dx = u - x;
    const double dy = v - y;
    const std::uint8_t* image = images + frame.image_offset;
    const std::size_t top_left = (static_cast<std::size_t>(y) * config.image_width + x) * 3;
    const std::size_t bottom_left = top_left + static_cast<std::size_t>(config.image_width) * 3;
    for (int channel = 0; channel < 3; ++channel) {
        const double top = image[top_left + channel] * (1.0 - dx)
                        + image[top_left + 3 + channel] * dx;
        const double bottom = image[bottom_left + channel] * (1.0 - dx)
                           + image[bottom_left + 3 + channel] * dx;
        rgb[channel] = static_cast<std::uint8_t>(fmin(255.0, fmax(
            0.0, nearbyint(top * (1.0 - dy) + bottom * dy))));
    }
}

__global__ void assignment_kernel(
        const double* xyz, std::size_t point_count, const DeviceFrame* frames,
        int frame_count, const DeviceConfig* config, const std::uint8_t* images,
        const float* depth, std::uint16_t* colors, float* quality,
        float* distances, std::uint16_t* sources, unsigned long long* counters) {
    const std::size_t point = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (point >= point_count) {
        return;
    }
    float best_quality = quality[point];
    float best_distance = distances[point];
    std::uint16_t best_source = sources[point];
    std::uint16_t best_colors[3] = {colors[point * 3], colors[point * 3 + 1], colors[point * 3 + 2]};
    const std::size_t depth_pixels = static_cast<std::size_t>(config->depth_width) * config->depth_height;

    for (int frame_index = 0; frame_index < frame_count; ++frame_index) {
        const DeviceFrame& frame = frames[frame_index];
        double camera_x, camera_y, camera_z, u, v;
        if (!project_point(xyz + point * 3, frame, *config, true,
                           camera_x, camera_y, camera_z, u, v)) {
            continue;
        }
        const int depth_x = static_cast<int>(u * config->depth_width / config->image_width);
        const int depth_y = static_cast<int>(v * config->depth_height / config->image_height);
        const float near_depth = depth[static_cast<std::size_t>(frame_index) * depth_pixels
            + static_cast<std::size_t>(depth_y) * config->depth_width + depth_x];
        if (camera_z > near_depth + config->occlusion_absolute
                + near_depth * config->occlusion_relative) {
            continue;
        }

        std::uint8_t rgb[3];
        sample_rgb(images, frame, *config, u, v, rgb);
        const double luminance = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
        bool usable = !config->reject_exposure_extremes
            || (luminance >= config->minimum_luminance && luminance <= config->maximum_luminance);
        if (config->illumination_balancing) {
            const std::uint8_t maximum_channel = max(rgb[0], max(rgb[1], rgb[2]));
            const bool exposure_ok = luminance >= fmaxf(3.0f, config->minimum_luminance)
                && luminance <= fminf(252.0f, config->maximum_luminance)
                && maximum_channel < 253;
            if (!exposure_ok) {
                atomicAdd(counters + 1, 1ULL);
            }
            usable = usable && exposure_ok;
        }
        const double distance = sqrt(camera_x * camera_x + camera_y * camera_y
                                   + camera_z * camera_z);
        if (config->maximum_distance > 0.0 && distance > config->maximum_distance) {
            usable = false;
        }
        if (!usable || distance <= 0.0) {
            continue;
        }
        const double cosine = camera_z / distance;
        const double border_distance = fmin(fmin(u / config->image_width, v / config->image_height),
            fmin(1.0 - u / config->image_width, 1.0 - v / config->image_height));
        const double edge_weight = fmin(1.0, fmax(0.05, border_distance / 0.15));
        const double exposure_weight = 0.5 + 0.5 *
            (1.0 - fabs(luminance - 127.5) / 127.5);
        float score = static_cast<float>(frame.sharpness_weight * cosine * cosine
                    * edge_weight * exposure_weight
                    / (1.0 + (camera_z / 5.0) * (camera_z / 5.0)));
        if (!(score > best_quality)) {
            continue;
        }

        std::uint8_t corrected[3] = {rgb[0], rgb[1], rgb[2]};
        if (frame.illumination_enabled) {
            const double scale_x = fmax(config->cx, config->image_width - config->cx);
            const double scale_y = fmax(config->cy, config->image_height - config->cy);
            const double radius = fmin(1.0, ((u - config->cx) * (u - config->cx)
                + (v - config->cy) * (v - config->cy)) / (scale_x * scale_x + scale_y * scale_y));
            double gain = exp(frame.radial * fmin(radius, frame.radius_limit) + frame.exposure);
            gain = fmin(1.35, fmax(0.75, gain));
            const std::uint8_t maximum_channel = max(rgb[0], max(rgb[1], rgb[2]));
            gain = fmin(gain, 1.0 / fmax(static_cast<double>(srgb_to_linear(maximum_channel)), 1.0e-9));
            const double penalty_base = fmin(gain, 1.0 / gain);
            score = static_cast<float>(score * penalty_base * penalty_base
                                       * (0.85 + 0.15 * frame.confidence));
            if (!(score > best_quality)) {
                continue;
            }
            bool changed = false;
            for (int channel = 0; channel < 3; ++channel) {
                corrected[channel] = linear_to_srgb(static_cast<float>(srgb_to_linear(rgb[channel]) * gain));
                changed = changed || corrected[channel] != rgb[channel];
            }
            if (changed) {
                atomicAdd(counters, 1ULL);
            }
        }
        best_quality = score;
        best_distance = static_cast<float>(distance);
        best_source = frame.source_flight;
        for (int channel = 0; channel < 3; ++channel) {
            best_colors[channel] = static_cast<std::uint16_t>(corrected[channel]) * 257;
        }
    }

    quality[point] = best_quality;
    distances[point] = best_distance;
    sources[point] = best_source;
    colors[point * 3] = best_colors[0];
    colors[point * 3 + 1] = best_colors[1];
    colors[point * 3 + 2] = best_colors[2];
}

int ensure_capacity(BatchContext* context, std::size_t count, char* error,
                    std::uint32_t error_length) {
    if (count <= context->capacity) {
        return 0;
    }
    cudaFree(context->device_sources); context->device_sources = nullptr;
    cudaFree(context->device_distances); context->device_distances = nullptr;
    cudaFree(context->device_quality); context->device_quality = nullptr;
    cudaFree(context->device_colors); context->device_colors = nullptr;
    cudaFree(context->device_xyz); context->device_xyz = nullptr;
    cudaError_t status = cudaMalloc(reinterpret_cast<void**>(&context->device_xyz), count * 3 * sizeof(double));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_colors), count * 3 * sizeof(std::uint16_t));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_quality), count * sizeof(float));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_distances), count * sizeof(float));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_sources), count * sizeof(std::uint16_t));
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA chunk buffer allocation failed", status);
        context->capacity = 0;
        return 20;
    }
    context->capacity = count;
    return 0;
}
}  // namespace

extern "C" __declspec(dllexport) int elios_cuda_probe(ProbeResult* result,
                                                       std::uint32_t size) {
    if (result == nullptr || size < sizeof(ProbeResult)) return -1;
    std::memset(result, 0, sizeof(*result));
    result->abi_version = kAbiVersion;
    result->selected_device = -1;
    cudaError_t error = cudaGetDeviceCount(&result->device_count);
    if (error != cudaSuccess) {
        result->status = 1;
        write_error(result->error, sizeof(result->error), "CUDA device discovery failed", error);
        return result->status;
    }
    if (result->device_count < 1) {
        result->status = 2;
        write_message(result->error, sizeof(result->error), "No NVIDIA CUDA device was found");
        return result->status;
    }
    result->selected_device = 0;
    if ((error = cudaSetDevice(0)) != cudaSuccess) {
        result->status = 3;
        write_error(result->error, sizeof(result->error), "CUDA device selection failed", error);
        return result->status;
    }
    cudaDeviceProp properties{};
    if ((error = cudaGetDeviceProperties(&properties, 0)) != cudaSuccess) {
        result->status = 4;
        write_error(result->error, sizeof(result->error), "CUDA device properties failed", error);
        return result->status;
    }
    std::snprintf(result->device_name, sizeof(result->device_name), "%s", properties.name);
    result->compute_major = properties.major;
    result->compute_minor = properties.minor;
    result->multiprocessor_count = properties.multiProcessorCount;
    cudaDriverGetVersion(&result->driver_version);
    cudaRuntimeGetVersion(&result->runtime_version);
    std::size_t free_memory = 0, total_memory = 0;
    if ((error = cudaMemGetInfo(&free_memory, &total_memory)) != cudaSuccess) {
        result->status = 5;
        write_error(result->error, sizeof(result->error), "CUDA memory query failed", error);
        return result->status;
    }
    result->free_memory_bytes = free_memory;
    result->total_memory_bytes = total_memory;

    std::vector<float> input(kProbeElementCount), output(kProbeElementCount, 0.0f);
    for (int index = 0; index < kProbeElementCount; ++index) input[index] = static_cast<float>(index % 251);
    float* device_input = nullptr;
    float* device_output = nullptr;
    const std::size_t bytes = input.size() * sizeof(float);
    error = cudaMalloc(reinterpret_cast<void**>(&device_input), bytes);
    if (error == cudaSuccess) error = cudaMalloc(reinterpret_cast<void**>(&device_output), bytes);
    if (error == cudaSuccess) error = cudaMemcpy(device_input, input.data(), bytes, cudaMemcpyHostToDevice);
    if (error == cudaSuccess) {
        checkpoint_kernel<<<16, 256>>>(device_input, device_output, kProbeElementCount);
        error = cudaGetLastError();
    }
    if (error == cudaSuccess) error = cudaDeviceSynchronize();
    if (error == cudaSuccess) error = cudaMemcpy(output.data(), device_output, bytes, cudaMemcpyDeviceToHost);
    cudaFree(device_output);
    cudaFree(device_input);
    if (error != cudaSuccess) {
        result->status = 6;
        write_error(result->error, sizeof(result->error), "CUDA checkpoint execution failed", error);
        return result->status;
    }
    float max_error = 0.0f;
    for (int index = 0; index < kProbeElementCount; ++index) {
        max_error = std::max(max_error, std::fabs(output[index] - (input[index] * 2.0f + 1.0f)));
    }
    result->self_test_max_error = max_error;
    if (max_error != 0.0f) {
        result->status = 7;
        write_message(result->error, sizeof(result->error), "CUDA checkpoint returned an incorrect result");
    }
    return result->status;
}

extern "C" __declspec(dllexport) int elios_cuda_batch_create(
        const ProjectionConfigInput* input_config,
        const ProjectionFrameInput* input_frames,
        const std::uint8_t* const* input_images, int frame_count,
        void** output_context, char* error, std::uint32_t error_length) {
    if (input_config == nullptr || input_frames == nullptr || input_images == nullptr
            || output_context == nullptr || input_config->abi_version != kAbiVersion
            || frame_count < 1) {
        write_message(error, error_length, "Invalid CUDA projection batch arguments");
        return 10;
    }
    *output_context = nullptr;
    BatchContext* context = new (std::nothrow) BatchContext();
    if (context == nullptr) {
        write_message(error, error_length, "CUDA projection context allocation failed");
        return 11;
    }
    context->frame_count = frame_count;
    DeviceConfig& config = context->host_config;
    config.image_width = input_config->image_width;
    config.image_height = input_config->image_height;
    config.depth_width = input_config->depth_width;
    config.depth_height = input_config->depth_height;
    config.distortion_model = input_config->distortion_model;
    config.coefficient_count = input_config->coefficient_count;
    config.fx = input_config->fx; config.fy = input_config->fy;
    config.cx = input_config->cx; config.cy = input_config->cy;
    std::memcpy(config.coefficients, input_config->coefficients, sizeof(config.coefficients));
    config.minimum_depth = input_config->minimum_depth;
    config.maximum_depth = input_config->maximum_depth;
    config.border_fraction = input_config->border_fraction;
    config.minimum_luminance = input_config->minimum_luminance;
    config.maximum_luminance = input_config->maximum_luminance;
    config.maximum_distance = input_config->maximum_distance;
    config.occlusion_absolute = input_config->occlusion_absolute;
    config.occlusion_relative = input_config->occlusion_relative;
    config.reject_exposure_extremes = input_config->reject_exposure_extremes;
    config.illumination_balancing = input_config->illumination_balancing;
    context->host_frames.resize(frame_count);
    const std::size_t image_stride = static_cast<std::size_t>(config.image_width) * config.image_height * 3;
    for (int index = 0; index < frame_count; ++index) {
        DeviceFrame& frame = context->host_frames[index];
        std::memcpy(frame.center, input_frames[index].center, sizeof(frame.center));
        std::memcpy(frame.rotation, input_frames[index].rotation, sizeof(frame.rotation));
        frame.sharpness_weight = input_frames[index].sharpness_weight;
        frame.source_flight = input_frames[index].source_flight;
        frame.image_offset = static_cast<std::uint64_t>(index) * image_stride;
        frame.illumination_enabled = 0;
    }
    cudaError_t status = cudaSetDevice(0);
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_config), sizeof(DeviceConfig));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_frames), frame_count * sizeof(DeviceFrame));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_images), image_stride * frame_count);
    const std::size_t depth_count = static_cast<std::size_t>(config.depth_width) * config.depth_height * frame_count;
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_depth), depth_count * sizeof(float));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_depth_temp), depth_count * sizeof(float));
    if (status == cudaSuccess) status = cudaMalloc(reinterpret_cast<void**>(&context->device_counters), 2 * sizeof(unsigned long long));
    if (status == cudaSuccess) status = cudaMemcpy(context->device_config, &config, sizeof(config), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemcpy(context->device_frames, context->host_frames.data(),
                                                   frame_count * sizeof(DeviceFrame), cudaMemcpyHostToDevice);
    for (int index = 0; status == cudaSuccess && index < frame_count; ++index) {
        status = cudaMemcpy(context->device_images + static_cast<std::size_t>(index) * image_stride,
                            input_images[index], image_stride, cudaMemcpyHostToDevice);
    }
    if (status == cudaSuccess) {
        initialize_depth<<<static_cast<unsigned int>((depth_count + 255) / 256), 256>>>(context->device_depth, depth_count);
        status = cudaGetLastError();
    }
    if (status == cudaSuccess) status = cudaDeviceSynchronize();
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA projection batch initialization failed", status);
        delete context;
        return 12;
    }
    *output_context = context;
    return 0;
}

extern "C" __declspec(dllexport) int elios_cuda_visibility(
        void* opaque, const double* xyz, std::uint64_t count,
        char* error, std::uint32_t error_length) {
    BatchContext* context = static_cast<BatchContext*>(opaque);
    if (context == nullptr || xyz == nullptr || count == 0) return 13;
    int result = ensure_capacity(context, static_cast<std::size_t>(count), error, error_length);
    if (result != 0) return result;
    cudaError_t status = cudaMemcpy(context->device_xyz, xyz, count * 3 * sizeof(double), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) {
        const dim3 grid(static_cast<unsigned int>((count + 255) / 256), context->frame_count);
        visibility_kernel<<<grid, 256>>>(context->device_xyz, static_cast<std::size_t>(count),
            context->device_frames, context->frame_count, context->device_config, context->device_depth);
        status = cudaGetLastError();
    }
    if (status == cudaSuccess) status = cudaDeviceSynchronize();
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA visibility projection failed", status);
        return 14;
    }
    return 0;
}

extern "C" __declspec(dllexport) int elios_cuda_finish_visibility(
        void* opaque, int radius, char* error, std::uint32_t error_length) {
    BatchContext* context = static_cast<BatchContext*>(opaque);
    if (context == nullptr || radius < 0 || radius > 4) return 15;
    if (radius == 0) return 0;
    const std::size_t total = static_cast<std::size_t>(context->host_config.depth_width)
        * context->host_config.depth_height * context->frame_count;
    erode_depth_kernel<<<static_cast<unsigned int>((total + 255) / 256), 256>>>(
        context->device_depth, context->device_depth_temp, radius,
        context->host_config.depth_width, context->host_config.depth_height, context->frame_count);
    cudaError_t status = cudaGetLastError();
    if (status == cudaSuccess) status = cudaDeviceSynchronize();
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA depth-buffer expansion failed", status);
        return 16;
    }
    std::swap(context->device_depth, context->device_depth_temp);
    return 0;
}

extern "C" __declspec(dllexport) int elios_cuda_get_depth(
        void* opaque, int frame_index, float* output,
        char* error, std::uint32_t error_length) {
    BatchContext* context = static_cast<BatchContext*>(opaque);
    if (context == nullptr || output == nullptr || frame_index < 0 || frame_index >= context->frame_count) return 17;
    const std::size_t pixels = static_cast<std::size_t>(context->host_config.depth_width)
        * context->host_config.depth_height;
    const cudaError_t status = cudaMemcpy(output, context->device_depth
        + static_cast<std::size_t>(frame_index) * pixels, pixels * sizeof(float), cudaMemcpyDeviceToHost);
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA depth-buffer download failed", status);
        return 18;
    }
    return 0;
}

extern "C" __declspec(dllexport) int elios_cuda_set_illumination(
        void* opaque, const IlluminationInput* illumination, int frame_count,
        char* error, std::uint32_t error_length) {
    BatchContext* context = static_cast<BatchContext*>(opaque);
    if (context == nullptr || illumination == nullptr || frame_count != context->frame_count) return 19;
    for (int index = 0; index < frame_count; ++index) {
        DeviceFrame& frame = context->host_frames[index];
        frame.illumination_enabled = illumination[index].enabled;
        frame.radial = illumination[index].radial;
        frame.exposure = illumination[index].exposure;
        frame.confidence = illumination[index].confidence;
        frame.radius_limit = illumination[index].radius_limit;
    }
    const cudaError_t status = cudaMemcpy(context->device_frames, context->host_frames.data(),
        frame_count * sizeof(DeviceFrame), cudaMemcpyHostToDevice);
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA illumination parameters failed", status);
        return 21;
    }
    return 0;
}

extern "C" __declspec(dllexport) int elios_cuda_assign(
        void* opaque, const double* xyz, std::uint64_t count,
        std::uint16_t* colors, float* quality, float* distances,
        std::uint16_t* sources, std::uint64_t* corrected_count,
        std::uint64_t* rejected_count, char* error, std::uint32_t error_length) {
    BatchContext* context = static_cast<BatchContext*>(opaque);
    if (context == nullptr || xyz == nullptr || colors == nullptr || quality == nullptr
            || distances == nullptr || sources == nullptr || count == 0) return 22;
    int result = ensure_capacity(context, static_cast<std::size_t>(count), error, error_length);
    if (result != 0) return result;
    cudaError_t status = cudaMemcpy(context->device_xyz, xyz, count * 3 * sizeof(double), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemcpy(context->device_colors, colors, count * 3 * sizeof(std::uint16_t), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemcpy(context->device_quality, quality, count * sizeof(float), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemcpy(context->device_distances, distances, count * sizeof(float), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemcpy(context->device_sources, sources, count * sizeof(std::uint16_t), cudaMemcpyHostToDevice);
    if (status == cudaSuccess) status = cudaMemset(context->device_counters, 0, 2 * sizeof(unsigned long long));
    if (status == cudaSuccess) {
        assignment_kernel<<<static_cast<unsigned int>((count + 255) / 256), 256>>>(
            context->device_xyz, static_cast<std::size_t>(count), context->device_frames,
            context->frame_count, context->device_config, context->device_images,
            context->device_depth, context->device_colors, context->device_quality,
            context->device_distances, context->device_sources, context->device_counters);
        status = cudaGetLastError();
    }
    if (status == cudaSuccess) status = cudaDeviceSynchronize();
    if (status == cudaSuccess) status = cudaMemcpy(colors, context->device_colors, count * 3 * sizeof(std::uint16_t), cudaMemcpyDeviceToHost);
    if (status == cudaSuccess) status = cudaMemcpy(quality, context->device_quality, count * sizeof(float), cudaMemcpyDeviceToHost);
    if (status == cudaSuccess) status = cudaMemcpy(distances, context->device_distances, count * sizeof(float), cudaMemcpyDeviceToHost);
    if (status == cudaSuccess) status = cudaMemcpy(sources, context->device_sources, count * sizeof(std::uint16_t), cudaMemcpyDeviceToHost);
    unsigned long long counters[2] = {0, 0};
    if (status == cudaSuccess) status = cudaMemcpy(counters, context->device_counters,
                                                   sizeof(counters), cudaMemcpyDeviceToHost);
    if (status != cudaSuccess) {
        write_error(error, error_length, "CUDA color assignment failed", status);
        return 23;
    }
    if (corrected_count != nullptr) *corrected_count = counters[0];
    if (rejected_count != nullptr) *rejected_count = counters[1];
    return 0;
}

extern "C" __declspec(dllexport) void elios_cuda_batch_destroy(void* opaque) {
    delete static_cast<BatchContext*>(opaque);
}
