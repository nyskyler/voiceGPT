#!/bin/bash
export FLASK_APP=voiceGPT
export FLASK_ENV=development
export FLASK_DEBUG=true
export FLASK_RUN_PORT=8080
export FLASK_RUN_THREADED=true
echo 'export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"' >> ~/.zshrc
echo 'export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_LIBRARY_PATH"' >> ~/.zshrc
source ~/.zshrc 
source /Users/yuseung-u/Desktop/pythonLab/voiceGPT/bin/activate 
cd /Users/yuseung-u/Desktop/pythonLab/voiceGPT

