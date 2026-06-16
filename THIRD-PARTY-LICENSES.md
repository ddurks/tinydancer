# Third-party licenses

This repository contains only the original "glue" code (the `*.py` / `*.sh`
scripts and docs), which is MIT-licensed — see [LICENSE](LICENSE).

It does **not** include or redistribute any model weights, checkpoints, or body
models. Those are downloaded onto your own machine / GPU instance at runtime by
`setup.sh` and `aws/launch.sh`. Each is governed by **its own license**, several
of which **restrict use to non-commercial research**. By running this pipeline
you are downloading and using those assets directly, and you are responsible for
reviewing and complying with their terms. The MIT license on this repo's code
grants you **no rights** to any of them.

| Component | What it is | Where to read the license |
| --- | --- | --- |
| **SMPL-X** (`SMPLX_NEUTRAL.npz`) | Body model used for inference | <https://smpl-x.is.tue.mpg.de/> — register and accept the license; **non-commercial research use only**. |
| **GVHMR** | Stage-1 motion estimator (cloned at runtime) | <https://github.com/zju3dv/GVHMR> — see its `LICENSE`; research/non-commercial terms apply, and it itself depends on SMPL/SMPL-X. |
| **HMR2 / 4DHumans** | Bundled by GVHMR | <https://github.com/shubham-goel/4D-Humans> |
| **ViTPose** | Bundled by GVHMR | <https://github.com/ViTAE-Transformer/ViTPose> |
| **YOLOv8** (Ultralytics) | Person detector | <https://github.com/ultralytics/ultralytics> — AGPL-3.0 (commercial use requires an Ultralytics license). |
| **Checkpoint mirror** (`camenduru/GVHMR`) | Convenience mirror of the above weights | <https://huggingface.co/camenduru/GVHMR> — weights remain under their original licenses. |
| **Rokoko Studio Live (Blender addon)** | Optional retargeting helper | <https://github.com/Rokoko/rokoko-studio-live-blender> |

**Bottom line:** this repo's code is free to use under MIT, but the SMPL-X and
GVHMR ecosystems are largely **non-commercial research** licensed. If you intend
to use the end-to-end pipeline commercially, you must obtain the appropriate
licenses for those components yourself.
