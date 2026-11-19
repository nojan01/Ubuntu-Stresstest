Synthetic video-test vectors generated with FFmpeg's `testsrc2` source.
No downloaded or third-party video content. Each clip has 30 frames at
1280x720, 30 fps, 8-bit YUV 4:2:0, encoded with libx264/libx265/libaom-av1.
Regenerate with `python3 scripts/make_video_vectors.py` (requires FFmpeg
with those three CPU encoders); this also updates manifest SHA-256 hashes.

These short vectors validate this format only, not every resolution,
bit depth or codec profile. CPU decoding provides frame hashes for the
independent NVDEC comparison. NVENC output is decoded on the CPU and
checked for frame count and SSIM >= 0.95 against the decoded source.
The quality threshold is a test criterion, not proof of defective hardware.

Optional load streams rotate over successful codec/direction combinations
every ten seconds. FFmpeg fps/frame counters and NVIDIA telemetry are logged
when available. GPU targets use UUIDs through CUDA_VISIBLE_DEVICES and local
device ordinal zero. No automatic software fallback for GPU operations.
