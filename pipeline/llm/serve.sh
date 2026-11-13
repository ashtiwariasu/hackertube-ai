#!/bin/bash
# vLLM OpenAI-compatible server for labelling. Thinking disabled per request via chat_template_kwargs.
cd ~/llm && . venv/bin/activate
export VLLM_USE_DEEP_GEMM=0
exec python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-32B-FP8 --served-model-name labeller \
  --max-model-len 6144 --gpu-memory-utilization 0.88 --max-num-seqs 128 --disable-log-stats --port 8100 --host 127.0.0.1 \
 
