#include <cmath>
#include <cstdint>

#ifdef _WIN32
#define ELIOS_EXPORT extern "C" __declspec(dllexport)
#else
#define ELIOS_EXPORT extern "C"
#endif

namespace {
bool project(double x, double y, int model, const double* c, int count,
             double& px, double& py) {
    px = x; py = y;
    if (model == 0) return true;
    if (model == 1) {
        const double r2=x*x+y*y, r4=r2*r2, r6=r4*r2;
        const double k1=count>0?c[0]:0, k2=count>1?c[1]:0;
        const double p1=count>2?c[2]:0, p2=count>3?c[3]:0;
        const double k3=count>4?c[4]:0, k4=count>5?c[5]:0;
        const double k5=count>6?c[6]:0, k6=count>7?c[7]:0;
        const double s1=count>8?c[8]:0, s2=count>9?c[9]:0;
        const double s3=count>10?c[10]:0, s4=count>11?c[11]:0;
        const double denominator=1+k4*r2+k5*r4+k6*r6;
        if (std::abs(denominator)<1e-15) return false;
        const double radial=(1+k1*r2+k2*r4+k3*r6)/denominator;
        px=x*radial+2*p1*x*y+p2*(r2+2*x*x)+s1*r2+s2*r4;
        py=y*radial+p1*(r2+2*y*y)+2*p2*x*y+s3*r2+s4*r4;
        return true;
    }
    const double r=std::hypot(x,y);
    if (r<=1e-15) return true;
    const double theta=std::atan(r), t2=theta*theta, t4=t2*t2;
    const double distorted=theta*(1+(count>0?c[0]:0)*t2+(count>1?c[1]:0)*t4
        +(count>2?c[2]:0)*t4*t2+(count>3?c[3]:0)*t4*t4);
    px=x*distorted/r; py=y*distorted/r;
    return true;
}
}

ELIOS_EXPORT std::uint32_t elios_cpu_projection_abi() { return 1; }

ELIOS_EXPORT int elios_cpu_project(
        const double* xyz, std::uint64_t count, const double* center,
        const double* rotation, int model, const double* coefficients,
        int coefficient_count, double fx, double fy, double cx, double cy,
        int width, int height, double minimum_depth, double maximum_depth,
        double border_fraction, std::uint64_t* selected, double* uv,
        double* depths, double* camera, std::uint64_t* output_count) {
    if (!xyz || !center || !rotation || !selected || !uv || !depths
            || !camera || !output_count || count==0 || width<2 || height<2
            || model<0 || model>2 || coefficient_count<0 || coefficient_count>12) return 1;
    const double pad_x=width*border_fraction, pad_y=height*border_fraction;
    std::uint64_t output=0;
    for (std::uint64_t index=0; index<count; ++index) {
        const double dx=xyz[index*3]-center[0];
        const double dy=xyz[index*3+1]-center[1];
        const double dz=xyz[index*3+2]-center[2];
        const double x=dx*rotation[0]+dy*rotation[3]+dz*rotation[6];
        const double y=dx*rotation[1]+dy*rotation[4]+dz*rotation[7];
        const double z=dx*rotation[2]+dy*rotation[5]+dz*rotation[8];
        if (!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(z)
                ||z<minimum_depth||z>maximum_depth) continue;
        double px,py;
        if (!project(x/z,y/z,model,coefficients,coefficient_count,px,py)) continue;
        const double u=fx*px+cx, v=fy*py+cy;
        if (!std::isfinite(u)||!std::isfinite(v)||u<pad_x||v<pad_y
                ||u>=width-1-pad_x||v>=height-1-pad_y) continue;
        selected[output]=index; uv[output*2]=u; uv[output*2+1]=v;
        depths[output]=z; camera[output*3]=x; camera[output*3+1]=y;
        camera[output*3+2]=z; ++output;
    }
    *output_count=output;
    return 0;
}
