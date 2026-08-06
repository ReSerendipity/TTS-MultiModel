GITIGNORE update recommendation for ReSerendipity/TTS_MultiModel

Note: This repository intentionally allows personas/*.pt to be tracked (there is an explicit exception in .gitignore). This branch will NOT remove that exception.

Suggested additions to .gitignore (append if desired):

# model weights
*.safetensors
*.pth
*.ckpt
*.bin
*.onnx
*.gguf

# outputs / audio
outputs/
*.wav
*.mp3
*.flac

# runtime / logs
logs/
log.txt
data/*.db
*.db
.env
.env.*

# virtualenv / bundled env
WPy64-*/
python/

Instructions:
1) To apply locally, run:
   git checkout -b fix/update-gitignore
   # append the snippet to .gitignore (or edit as desired)
   git add .gitignore
   git commit -m "chore: augment .gitignore to ignore runtime and model artifacts"
   git push origin HEAD

2) Open a PR to merge.
