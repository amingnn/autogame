"""在自动化会话期间静音并在会话结束后恢复系统音量。"""

from __future__ import annotations

import ctypes
import platform
import uuid
from ctypes import wintypes

from autogame.logger import mlog


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    )


def _guid(value: str) -> _GUID:
    parsed = uuid.UUID(value)
    return _GUID(
        parsed.time_low,
        parsed.time_mid,
        parsed.time_hi_version,
        (wintypes.BYTE * 8).from_buffer_copy(parsed.bytes[8:]),
    )


class _ComInterface:
    """极小的 COM vtable 调用封装，避免额外引入音频依赖。"""

    def __init__(self, pointer: ctypes.c_void_p) -> None:
        self.pointer = pointer

    def call(self, index: int, restype: object, argtypes: list[object], *args: object):
        vtable = ctypes.cast(
            self.pointer,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        function = ctypes.WINFUNCTYPE(
            restype,
            ctypes.c_void_p,
            *argtypes,
        )(vtable[index])
        return function(self.pointer, *args)

    def release(self) -> None:
        if self.pointer:
            self.call(2, wintypes.HRESULT, [])
            self.pointer = ctypes.c_void_p()


class _WindowsMasterMute:
    """通过 Windows Core Audio 获取默认播放设备的静音状态。"""

    _CLSID_MM_DEVICE_ENUMERATOR = _guid(
        "BCDE0395-E52F-467C-8E3D-C4579291692E"
    )
    _IID_MM_DEVICE_ENUMERATOR = _guid(
        "A95664D2-9614-4F35-A746-DE8DB63617E6"
    )
    _IID_AUDIO_ENDPOINT_VOLUME = _guid(
        "5CDF2C82-841E-4546-9722-0CF74078229A"
    )
    _CLSCTX_ALL = 0x17

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("当前系统不是 Windows")
        self._ole32 = ctypes.windll.ole32
        self._co_initialized = False
        self._endpoint: _ComInterface | None = None
        self._volume: _ComInterface | None = None
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def _initialize(self) -> None:
        result = self._ole32.CoInitializeEx(None, 0x2)
        signed_result = ctypes.c_long(result).value
        if signed_result in (0, 1):  # S_OK / S_FALSE
            self._co_initialized = True
        elif signed_result != -2147417850:  # RPC_E_CHANGED_MODE: already initialized
            self._check_hresult(result, "初始化音频 COM")

        enumerator_pointer = ctypes.c_void_p()
        self._check_hresult(
            self._ole32.CoCreateInstance(
                ctypes.byref(self._CLSID_MM_DEVICE_ENUMERATOR),
                None,
                self._CLSCTX_ALL,
                ctypes.byref(self._IID_MM_DEVICE_ENUMERATOR),
                ctypes.byref(enumerator_pointer),
            ),
            "获取音频设备枚举器",
        )
        enumerator = _ComInterface(enumerator_pointer)
        try:
            endpoint_pointer = ctypes.c_void_p()
            self._check_hresult(
                enumerator.call(
                    4,
                    wintypes.HRESULT,
                    [wintypes.INT, wintypes.INT, ctypes.POINTER(ctypes.c_void_p)],
                    0,
                    0,
                    ctypes.byref(endpoint_pointer),
                ),
                "获取默认音频设备",
            )
            self._endpoint = _ComInterface(endpoint_pointer)
        finally:
            enumerator.release()

        volume_pointer = ctypes.c_void_p()
        try:
            self._check_hresult(
                self._endpoint.call(  # type: ignore[union-attr]
                    3,
                    wintypes.HRESULT,
                    [
                        ctypes.POINTER(_GUID),
                        wintypes.DWORD,
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p),
                    ],
                    ctypes.byref(self._IID_AUDIO_ENDPOINT_VOLUME),
                    self._CLSCTX_ALL,
                    None,
                    ctypes.byref(volume_pointer),
                ),
                "获取音量控制接口",
            )
            self._volume = _ComInterface(volume_pointer)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check_hresult(result: int, operation: str) -> None:
        value = ctypes.c_long(result).value
        if value < 0:
            raise OSError(f"{operation}失败，HRESULT=0x{value & 0xFFFFFFFF:08X}")

    def get_muted(self) -> bool:
        muted = wintypes.BOOL()
        self._check_hresult(
            self._volume.call(  # type: ignore[union-attr]
                15,
                wintypes.HRESULT,
                [ctypes.POINTER(wintypes.BOOL)],
                ctypes.byref(muted),
            ),
            "读取系统静音状态",
        )
        return bool(muted.value)

    def set_muted(self, muted: bool) -> None:
        self._check_hresult(
            self._volume.call(  # type: ignore[union-attr]
                14,
                wintypes.HRESULT,
                [wintypes.BOOL, ctypes.c_void_p],
                bool(muted),
                None,
            ),
            "设置系统静音状态",
        )

    def close(self) -> None:
        if self._volume:
            self._volume.release()
            self._volume = None
        if self._endpoint:
            self._endpoint.release()
            self._endpoint = None
        if self._co_initialized:
            self._ole32.CoUninitialize()
            self._co_initialized = False


class RuntimeMuteController:
    """保存并恢复自动化开始前的系统静音状态。"""

    def __init__(self) -> None:
        self._audio: _WindowsMasterMute | None = None
        self._previous_muted: bool | None = None
        self._changed = False

    def mute(self) -> None:
        """静音默认播放设备；失败时只记录警告，不阻止自动化。"""

        if self._previous_muted is not None:
            return
        if platform.system() != "Windows":
            mlog.warning("当前系统不支持运行时静音")
            return
        try:
            self._audio = _WindowsMasterMute()
            self._previous_muted = self._audio.get_muted()
            if not self._previous_muted:
                self._audio.set_muted(True)
                self._changed = True
            mlog.info("已开启自动化运行时静音")
        except Exception as exc:
            mlog.warning("开启运行时静音失败：{}", exc)
            self._close_audio()

    def restore(self) -> None:
        """恢复静音开关到自动化开始前的状态。"""

        try:
            if self._audio and self._previous_muted is not None and self._changed:
                self._audio.set_muted(self._previous_muted)
                mlog.info("已恢复自动化前的系统音量状态")
        except Exception as exc:
            mlog.warning("恢复系统音量状态失败：{}", exc)
        finally:
            self._close_audio()

    def _close_audio(self) -> None:
        if self._audio:
            self._audio.close()
            self._audio = None
