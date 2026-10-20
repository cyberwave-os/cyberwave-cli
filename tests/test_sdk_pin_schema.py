"""Device pin contract checks requiring the generated REST SDK."""

import pytest


@pytest.mark.parametrize("metadata", [
    {"video_device": "/dev/v4l/by-id/usb-camera-video-index0", "serial_number": ""},
    {"serial_number": "usb-microphone-00", "audio_device": "/dev/snd/by-id/usb-microphone-00"},
    {"serial_number": "", "video_device": ""},
])
def test_payloads_are_accepted_by_the_real_sdk_schema(metadata):
    from cyberwave.rest import TwinCreateSchema

    assert TwinCreateSchema(metadata=metadata).to_dict() == {"metadata": metadata}
