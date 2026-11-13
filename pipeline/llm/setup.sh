set -e
cd ~/llm
python3 -m venv venv
. venv/bin/activate
pip install -q --upgrade pip
pip install -q vllm huggingface_hub[cli]
python -c "import vllm; print(\"vllm\", vllm.__version__)"
hf download Qwen/Qwen3-32B-FP8 2>&1 | tail -2
echo SETUP_DONE
