"""Interactive GPU point-cloud/video viewport for calibration."""
from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .calibration_workbench import (
    CloudViewResult, _edge_alignment_score, project_visible_samples, render_overlay,
)

GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_PROGRAM_POINT_SIZE = 0x8642
GL_POINT_SPRITE = 0x8861
GL_FLOAT = 0x1406
GL_POINTS = 0x0000
GL_TRIANGLES = 0x0004
GL_TEXTURE0 = 0x84C0
GL_TEXTURE_2D = 0x0DE1
GL_RGBA32F = 0x8814

_POINT_VERTEX = """
#version 120
attribute vec3 position;
uniform mat3 worldToCamera;
uniform vec3 localToCamera;
uniform vec4 intrinsics;
uniform vec2 imageSize;
uniform vec2 pan;
uniform float zoom;
uniform float pointSize;
uniform float elevationLow;
uniform float elevationSpan;
uniform int distortionModel;
uniform float distortion[14];
varying float vDepth;
varying float vElevation;
vec2 distortOpenCV(vec2 p) {
    float x=p.x, y=p.y, r2=x*x+y*y, r4=r2*r2, r6=r4*r2;
    float radial=(1.0+distortion[0]*r2+distortion[1]*r4+distortion[4]*r6)
      /max(0.000001,1.0+distortion[5]*r2+distortion[6]*r4+distortion[7]*r6);
    vec2 distorted=vec2(x*radial+2.0*distortion[2]*x*y+distortion[3]*(r2+2.0*x*x)
      +distortion[8]*r2+distortion[9]*r4,
      y*radial+distortion[2]*(r2+2.0*y*y)+2.0*distortion[3]*x*y
      +distortion[10]*r2+distortion[11]*r4);
    float tx=distortion[12], ty=distortion[13];
    if(abs(tx)+abs(ty)<0.0000001) return distorted;
    float cx=cos(tx), sx=sin(tx), cy=cos(ty), sy=sin(ty);
    vec3 rx=vec3(distorted.x,cx*distorted.y+sx,-sx*distorted.y+cx);
    vec3 rotated=vec3(cy*rx.x-sy*rx.z,rx.y,sy*rx.x+cy*rx.z);
    return vec2(cy*cx*rotated.x+sy*cx*rotated.z,
                cy*cx*rotated.y-sx*rotated.z)/rotated.z;
}
vec2 distortFisheye(vec2 p) {
    float r=length(p); if(r<0.000001) return p;
    float t=atan(r), t2=t*t;
    float td=t*(1.0+distortion[0]*t2+distortion[1]*t2*t2
      +distortion[2]*t2*t2*t2+distortion[3]*t2*t2*t2*t2);
    return p*(td/r);
}
void main() {
    vec3 camera=worldToCamera*position+localToCamera;
    if(camera.z<=0.05||camera.z>=60.0) {
        gl_Position=vec4(2.0,2.0,2.0,1.0); gl_PointSize=0.0;
        vDepth=0.0; vElevation=0.0; return;
    }
    vec2 p=camera.xy/camera.z;
    if(distortionModel==1) p=distortOpenCV(p);
    if(distortionModel==2) p=distortFisheye(p);
    vec2 pixel=p*intrinsics.xy+intrinsics.zw;
    vec2 ndc=vec2(pixel.x/imageSize.x*2.0-1.0,1.0-pixel.y/imageSize.y*2.0);
    ndc=ndc*zoom+vec2(2.0*pan.x,-2.0*pan.y);
    float depth=log2(camera.z+1.0)/log2(61.0);
    gl_Position=vec4(ndc,depth*2.0-1.0,1.0); gl_PointSize=pointSize;
    vDepth=depth;
    vElevation=clamp((position.z-elevationLow)/elevationSpan,0.0,1.0);
}
"""

_POINT_FRAGMENT = """
#version 120
varying float vDepth; varying float vElevation;
void main() {
    vec2 d=gl_PointCoord-vec2(0.5); if(dot(d,d)>0.25) discard;
    gl_FragColor=vec4(vDepth,vElevation,1.0,1.0);
}
"""

_COMPOSITE_VERTEX = """
#version 120
attribute vec2 position; attribute vec2 texCoord; varying vec2 uv;
void main(){gl_Position=vec4(position,0.0,1.0);uv=texCoord;}
"""

_COMPOSITE_FRAGMENT = """
#version 120
uniform sampler2D geometryTexture; uniform sampler2D videoTexture;
uniform vec2 texel; uniform vec2 pan; uniform float zoom;
uniform float videoOpacity; uniform float edlStrength; uniform float edlRadius;
uniform int colorMode; varying vec2 uv;
vec3 turbo(float x) {
    x=clamp(x,0.0,1.0);
    vec4 kr=vec4(.13572138,4.61539260,-42.66032258,132.13108234);
    vec2 kr2=vec2(-152.94239396,59.28637943);
    vec4 kg=vec4(.09140261,2.19418839,4.84296658,-14.18503333);
    vec2 kg2=vec2(4.27729857,2.82956604);
    vec4 kb=vec4(.10667330,12.64194608,-60.58204836,110.36276771);
    vec2 kb2=vec2(-89.90310912,27.34824973);
    vec4 v4=vec4(1.0,x,x*x,x*x*x); vec2 v2=v4.zw*v4.z;
    return vec3(dot(v4,kr)+dot(v2,kr2),dot(v4,kg)+dot(v2,kg2),
      dot(v4,kb)+dot(v2,kb2));
}
void main() {
    vec4 geometry=texture2D(geometryTexture,uv);
    vec2 sourceUv=vec2((uv.x-.5-pan.x)/zoom+.5,(uv.y-.5+pan.y)/zoom+.5);
    vec3 video=vec3(.03,.045,.06);
    if(sourceUv.x>=0.0&&sourceUv.x<=1.0&&sourceUv.y>=0.0&&sourceUv.y<=1.0)
      video=texture2D(videoTexture,sourceUv).rgb;
    if(geometry.b<.5){gl_FragColor=vec4(video,1.0);return;}
    vec3 cloud=colorMode==0?turbo(geometry.g):(colorMode==1?vec3(.95,.58,.08):vec3(.78,.83,.87));
    float response=0.0;
    for(int x=-1;x<=1;++x)for(int y=-1;y<=1;++y){
      if(x==0&&y==0)continue;
      vec4 n=texture2D(geometryTexture,uv+vec2(float(x),float(y))*texel*edlRadius);
      response+=n.b<.5?.06:max(0.0,n.r-geometry.r);
    }
    float shade=exp(-edlStrength*response*10.0);
    cloud*=.38+.62*shade;
    gl_FragColor=vec4(mix(cloud,video,videoOpacity),1.0);
}
"""


class CalibrationViewport(QOpenGLWidget):
    """GPU point renderer with depth-tested splats and screen-space EDL."""

    rendered = Signal(object)
    image_picked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(620, 380)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Wheel: zoom in or out · drag: pan · double-click: reset view. "
            "Zoom out while cloud picking to reach geometry beyond the video frame.")
        self.frame = None
        self.xyz: np.ndarray | None = None
        self.calibration = None
        self.display_mode = "composite"
        self.video_opacity = .55
        self.point_size = 2
        self.color_mode = "amber"
        self.edl_strength = 1.2
        self.edl_radius = 1.0
        self.zoom = 1.0
        self.pan_fraction = np.zeros(2, dtype=np.float64)
        self._diagnostic_image: QImage | None = None
        self._error = ""
        self._last_mouse: QPoint | None = None
        self.pick_mode: str | None = None
        self.match_markers: list[tuple[tuple[float, float], tuple[float, float] | None,
                                           str, bool]] = []
        self._geometry_program = None
        self._composite_program = None
        self._full_buffer = None
        self._preview_buffer = None
        self._quad_buffer = None
        self._geometry_fbo = None
        self._video_texture = None
        self._uploaded_xyz_id = None
        self._uploaded_frame_id = None
        self._full_count = self._preview_count = 0
        self._origin = np.zeros(3, dtype=np.float64)
        self._elevation = (0.0, 1.0)
        self._interactive = True
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(180)
        self._detail_timer.timeout.connect(self._show_full_detail)
        self._diagnostic_timer = QTimer(self)
        self._diagnostic_timer.setSingleShot(True)
        self._diagnostic_timer.setInterval(100)
        self._diagnostic_timer.timeout.connect(self._render_diagnostic)

    def set_scene(self, frame: Any, xyz: np.ndarray, calibration: Any) -> None:
        if self.xyz is not xyz:
            self._uploaded_xyz_id = None
        if self.frame is None or self.frame.rgb is not frame.rgb:
            self._uploaded_frame_id = None
        self.frame, self.xyz, self.calibration = frame, xyz, calibration
        self.schedule_render()

    def set_pick_mode(self, mode: str | None) -> None:
        """Use the next left click as an original-frame pixel selection."""
        self.pick_mode = mode
        if mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    def set_match_markers(self, markers) -> None:
        self.match_markers = list(markers)
        self.update()

    def set_display_mode(self, mode: str) -> None:
        self.display_mode = mode
        self.schedule_render()

    def set_video_opacity(self, value: int) -> None:
        self.video_opacity = float(np.clip(value / 100, 0, 1))
        self.schedule_render()

    def set_point_size(self, value: int) -> None:
        self.point_size = int(np.clip(value, 1, 8))
        self.schedule_render()

    def set_color_mode(self, value: str) -> None:
        self.color_mode = value
        self.schedule_render()

    def set_edl_strength(self, value: int) -> None:
        self.edl_strength = value / 100
        self.schedule_render()

    def set_edl_radius(self, value: int) -> None:
        self.edl_radius = float(np.clip(value, 1, 4))
        self.schedule_render()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.pan_fraction[:] = 0
        self.schedule_render()

    def schedule_render(self) -> None:
        self._interactive = True
        self._detail_timer.start()
        if self.display_mode == "diagnostic":
            self._diagnostic_timer.start()
        self.update()

    def _show_full_detail(self) -> None:
        self._interactive = False
        self.update()
        self._emit_render_stats()

    def initializeGL(self) -> None:
        try:
            self._geometry_program = self._program(_POINT_VERTEX, _POINT_FRAGMENT, ((0, "position"),))
            self._composite_program = self._program(
                _COMPOSITE_VERTEX, _COMPOSITE_FRAGMENT, ((0, "position"), (1, "texCoord")))
            self._full_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._preview_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._quad_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            for buffer in (self._full_buffer, self._preview_buffer, self._quad_buffer):
                buffer.create()
                buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            quad = np.array([-1,-1,0,0, 1,-1,1,0, 1,1,1,1,
                             -1,-1,0,0, 1,1,1,1, -1,1,0,1], dtype=np.float32)
            self._quad_buffer.bind()
            self._quad_buffer.allocate(quad.tobytes(), quad.nbytes)
            self._quad_buffer.release()
        except Exception as exc:
            self._error = f"GPU calibration renderer could not initialize: {exc}"

    @staticmethod
    def _program(vertex, fragment, attributes):
        program = QOpenGLShaderProgram()
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
            raise RuntimeError(program.log())
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
            raise RuntimeError(program.log())
        for location, name in attributes:
            program.bindAttributeLocation(name, location)
        if not program.link():
            raise RuntimeError(program.log())
        return program

    def _upload_points(self) -> None:
        if self.xyz is None or self._uploaded_xyz_id == id(self.xyz):
            return
        points = np.asarray(self.xyz, dtype=np.float64)
        self._origin = np.median(points, axis=0)
        local = np.ascontiguousarray(points - self._origin, dtype=np.float32)
        stride = max(1, int(np.ceil(len(local) / 600_000)))
        preview = np.ascontiguousarray(local[::stride])
        for buffer, values in ((self._full_buffer, local), (self._preview_buffer, preview)):
            buffer.bind()
            buffer.allocate(values.tobytes(), values.nbytes)
            buffer.release()
        self._full_count, self._preview_count = len(local), len(preview)
        low, high = np.percentile(points[:, 2], (1, 99)) if len(points) > 5 else (0, 1)
        self._elevation = (float(low - self._origin[2]), max(float(high-low), 1e-5))
        self._uploaded_xyz_id = id(self.xyz)

    def _upload_video(self) -> None:
        if self.frame is None or self._uploaded_frame_id == id(self.frame.rgb):
            return
        if self._video_texture is not None:
            self._video_texture.destroy()
        rgb = np.ascontiguousarray(self.frame.rgb)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                       QImage.Format.Format_RGB888).copy().mirrored()
        self._video_texture = QOpenGLTexture(image)
        self._video_texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        self._video_texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        self._video_texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._uploaded_frame_id = id(self.frame.rgb)

    def _ensure_fbo(self, size: QSize) -> None:
        if self._geometry_fbo is not None and self._geometry_fbo.size() == size:
            return
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        fmt.setInternalTextureFormat(GL_RGBA32F)
        self._geometry_fbo = QOpenGLFramebufferObject(size, fmt)
        if not self._geometry_fbo.isValid():
            raise RuntimeError("The graphics driver could not create the calibration depth buffer")

    def _viewport_rect(self):
        dpr = self.devicePixelRatioF()
        total_w, total_h = max(2, round(self.width()*dpr)), max(2, round(self.height()*dpr))
        aspect = self.calibration.image_width / self.calibration.image_height
        if total_w / total_h > aspect:
            height, width = total_h, max(2, round(total_h*aspect))
            return (total_w-width)//2, 0, width, height
        width, height = total_w, max(2, round(total_w/aspect))
        return 0, (total_h-height)//2, width, height

    def paintGL(self) -> None:
        if (self.frame is None or self.xyz is None or self.calibration is None
                or self._geometry_program is None or self._composite_program is None):
            self._paint_message()
            return
        if self.display_mode == "diagnostic":
            self._paint_diagnostic()
            return
        try:
            self._upload_points()
            self._upload_video()
            functions = self.context().functions()
            x, y, width, height = self._viewport_rect()
            self._ensure_fbo(QSize(width, height))
            self._geometry_fbo.bind()
            functions.glViewport(0, 0, width, height)
            functions.glClearColor(0, 0, 0, 0)
            functions.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            functions.glEnable(GL_DEPTH_TEST)
            functions.glEnable(GL_PROGRAM_POINT_SIZE)
            functions.glEnable(GL_POINT_SPRITE)
            self._draw_points(functions)
            self._geometry_fbo.release()
            functions.glViewport(0, 0, round(self.width()*self.devicePixelRatioF()),
                                 round(self.height()*self.devicePixelRatioF()))
            functions.glClearColor(.03, .045, .06, 1)
            functions.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            functions.glDisable(GL_DEPTH_TEST)
            functions.glViewport(x, y, width, height)
            self._draw_composite(functions, width, height)
            self._paint_match_markers()
            self._error = ""
        except Exception as exc:
            self._error = str(exc)
            self._paint_message()

    def _draw_points(self, functions) -> None:
        center, camera_to_world = self.calibration.camera_pose(
            self.frame.position_world_m, self.frame.orientation_xyzw,
            self.frame.camera_pitch_degrees)
        matrix = camera_to_world.T.astype(np.float32)
        offset = (matrix @ (self._origin-center)).astype(np.float32)
        program = self._geometry_program
        program.bind()
        functions.glUniformMatrix3fv(program.uniformLocation("worldToCamera"), 1, False,
                                     matrix.T.ravel().tolist())
        functions.glUniform3fv(program.uniformLocation("localToCamera"), 1, offset.tolist())
        functions.glUniform4f(program.uniformLocation("intrinsics"), self.calibration.fx,
                              self.calibration.fy, self.calibration.cx, self.calibration.cy)
        functions.glUniform2f(program.uniformLocation("imageSize"),
                              self.calibration.image_width, self.calibration.image_height)
        functions.glUniform2f(program.uniformLocation("pan"), *self.pan_fraction)
        functions.glUniform1f(program.uniformLocation("zoom"), self.zoom)
        functions.glUniform1f(program.uniformLocation("pointSize"), self.point_size)
        functions.glUniform1f(program.uniformLocation("elevationLow"), self._elevation[0])
        functions.glUniform1f(program.uniformLocation("elevationSpan"), self._elevation[1])
        model = {"pinhole": 0, "opencv": 1, "fisheye": 2}.get(
            self.calibration.distortion_model, 0)
        functions.glUniform1i(program.uniformLocation("distortionModel"), model)
        coefficients = np.zeros(14, dtype=np.float32)
        supplied = np.asarray(self.calibration.distortion_coefficients, dtype=np.float32)
        coefficients[:min(14, len(supplied))] = supplied[:14]
        functions.glUniform1fv(program.uniformLocation("distortion"), 14, coefficients.tolist())
        buffer = self._preview_buffer if self._interactive else self._full_buffer
        count = self._preview_count if self._interactive else self._full_count
        buffer.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        functions.glDrawArrays(GL_POINTS, 0, count)
        program.disableAttributeArray(0)
        buffer.release()
        program.release()

    def _draw_composite(self, functions, width, height) -> None:
        program = self._composite_program
        program.bind()
        functions.glActiveTexture(GL_TEXTURE0)
        functions.glBindTexture(GL_TEXTURE_2D, self._geometry_fbo.texture())
        functions.glUniform1i(program.uniformLocation("geometryTexture"), 0)
        self._video_texture.bind(1)
        functions.glUniform1i(program.uniformLocation("videoTexture"), 1)
        functions.glUniform2f(program.uniformLocation("texel"), 1/max(1,width), 1/max(1,height))
        functions.glUniform2f(program.uniformLocation("pan"), *self.pan_fraction)
        functions.glUniform1f(program.uniformLocation("zoom"), self.zoom)
        functions.glUniform1f(program.uniformLocation("videoOpacity"), self.video_opacity)
        functions.glUniform1f(program.uniformLocation("edlStrength"), self.edl_strength)
        functions.glUniform1f(program.uniformLocation("edlRadius"), self.edl_radius)
        functions.glUniform1i(program.uniformLocation("colorMode"),
                              {"elevation":0,"amber":1,"neutral":2}.get(self.color_mode,1))
        self._quad_buffer.bind()
        program.enableAttributeArray(0); program.enableAttributeArray(1)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 16)
        program.setAttributeBuffer(1, GL_FLOAT, 8, 2, 16)
        functions.glDrawArrays(GL_TRIANGLES, 0, 6)
        program.disableAttributeArray(0); program.disableAttributeArray(1)
        self._quad_buffer.release()
        self._video_texture.release(1)
        program.release()

    def _render_diagnostic(self) -> None:
        if self.frame is None or self.xyz is None or self.calibration is None:
            return
        try:
            stride = max(1, int(np.ceil(len(self.xyz)/500_000)))
            result = render_overlay(self.frame, self.xyz[::stride], self.calibration,
                opacity=max(.1,1-self.video_opacity), point_radius=max(1,self.point_size//2),
                preview_width=int(np.clip(max(self.width(),640),640,1600)))
            rgb = np.ascontiguousarray(result.rgb)
            self._diagnostic_image = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.rendered.emit(result)
            self._error = ""
        except Exception as exc:
            self._error = str(exc)
        self.update()

    def _emit_render_stats(self) -> None:
        if self.frame is None or self.xyz is None or self.calibration is None:
            return
        try:
            stride = max(1, int(np.ceil(len(self.xyz)/250_000)))
            sampled = self.xyz[::stride]
            uv, depth, _ = project_visible_samples(sampled, self.frame, self.calibration)
            score = _edge_alignment_score(self.frame.rgb, uv, depth, self.calibration)
            self.rendered.emit(CloudViewResult(np.empty((0,0,3),dtype=np.uint8),
                len(self.xyz), len(uv), min(len(self.xyz),int(len(uv)*stride)), score))
        except Exception:
            pass

    def _paint_diagnostic(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080c10"))
        if self._diagnostic_image is not None:
            target = self._diagnostic_image.size()
            target.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            painter.drawImage((self.width()-target.width())//2,
                              (self.height()-target.height())//2,
                              self._diagnostic_image.scaled(target))
        if self._error:
            painter.setPen(QColor("#ffb07a")); painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self._error)
        painter.end()

    def _paint_message(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080c10"))
        if self._error:
            painter.setPen(QColor("#ffb07a")); painter.drawText(
                self.rect().adjusted(20,20,-20,-20),
                Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap, self._error)
        painter.end()

    def _widget_to_image(self, position) -> tuple[float, float] | None:
        if self.calibration is None:
            return None
        dpr = self.devicePixelRatioF()
        left, top, width, height = self._viewport_rect()
        x, y = position.x()*dpr, position.y()*dpr
        if x < left or y < top or x >= left+width or y >= top+height:
            return None
        display_x = (x-left)/width
        display_y = (y-top)/height
        source_x = (display_x-.5-self.pan_fraction[0])/self.zoom+.5
        source_y = (display_y-.5-self.pan_fraction[1])/self.zoom+.5
        # A video observation must be a real image pixel. During the cloud half
        # of a correspondence, however, the correct 3D feature may currently
        # project beyond the image because the calibration is substantially
        # shifted. Buffered cloud geometry remains pickable in overscan.
        limit_low, limit_high = ((-.5, 1.5) if self.pick_mode == "cloud" else (0., 1.))
        if not (limit_low <= source_x <= limit_high
                and limit_low <= source_y <= limit_high):
            return None
        return (source_x*self.calibration.image_width,
                source_y*self.calibration.image_height)

    def _image_to_widget(self, image_xy: tuple[float, float]) -> tuple[float, float]:
        left, top, width, height = self._viewport_rect()
        source_x = image_xy[0]/self.calibration.image_width
        source_y = image_xy[1]/self.calibration.image_height
        display_x = (source_x-.5)*self.zoom+.5+self.pan_fraction[0]
        display_y = (source_y-.5)*self.zoom+.5+self.pan_fraction[1]
        dpr = self.devicePixelRatioF()
        return ((left+display_x*width)/dpr, (top+display_y*height)/dpr)

    def _paint_match_markers(self) -> None:
        if self.calibration is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.zoom < .999 or self.pick_mode == "cloud":
            top_left = self._image_to_widget((0., 0.))
            bottom_right = self._image_to_widget(
                (self.calibration.image_width, self.calibration.image_height))
            painter.setPen(QPen(QColor("#63d8e8"), 1.5, Qt.PenStyle.DashLine))
            painter.drawRect(round(top_left[0]), round(top_left[1]),
                             round(bottom_right[0]-top_left[0]),
                             round(bottom_right[1]-top_left[1]))
        for target, projected, label, active in self.match_markers:
            tx, ty = self._image_to_widget(target)
            target_color = QColor("#36d399" if active else "#8da2b5")
            painter.setPen(QPen(target_color, 2.2))
            painter.drawEllipse(QPoint(round(tx), round(ty)), 7, 7)
            painter.drawText(round(tx)+10, round(ty)-8, label)
            if projected is None:
                continue
            px, py = self._image_to_widget(projected)
            painter.setPen(QPen(QColor("#ffb347" if active else "#758493"), 2.0))
            painter.drawLine(round(px)-6, round(py), round(px)+6, round(py))
            painter.drawLine(round(px), round(py)-6, round(px), round(py)+6)
            painter.setPen(QPen(QColor("#f07178" if active else "#657585"), 1.5))
            painter.drawLine(round(tx), round(ty), round(px), round(py))
        painter.end()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.zoom = float(np.clip(
            self.zoom*(1.15 if event.angleDelta().y()>0 else 1/1.15), .55, 8))
        self.schedule_render(); event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.pick_mode:
            selected = self._widget_to_image(event.position())
            if selected is not None:
                self.image_picked.emit(*selected)
            event.accept()
            return
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._last_mouse=event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_mouse is None: return
        current=event.position().toPoint(); delta=current-self._last_mouse; self._last_mouse=current
        self.pan_fraction += (delta.x()/max(1,self.width()), delta.y()/max(1,self.height()))
        self.schedule_render(); event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_mouse=None; self.unsetCursor(); event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.reset_view(); event.accept()
