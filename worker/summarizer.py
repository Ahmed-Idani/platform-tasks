import json
import re
import sys
import time

from llama_cpp import Llama

import os

MODEL_PATH = os.getenv("MODEL_PATH", "models/Qwen3-1.7B-Instruct-Q8_0.gguf")
N_THREADS = int(os.getenv("N_THREADS","1"))# will match the CPU limit of the pod later
llm = Llama(
  model_path=MODEL_PATH, verbose=False,
  n_ctx=8192,     # max input + output tokens;
  n_threads=N_THREADS,
  seed=42,
)


def summarize(text: str, max_words: int = 150) -> dict:
  start = time.time()
  output = llm.create_chat_completion(
    messages=[
      {"role": "system", "content": "You summarize documents accurately and concisely."},
      {"role": "user", "content": f"Summarize the following text in at most {max_words} words. /no_think\n\n{text}"},
    ],
    max_tokens=max_words * 2,
    temperature=0,
  )
  content = output["choices"][0]["message"]["content"]
  summary = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
  usage = output["usage"]
  return {
    "summary": summary,
    "input_tokens": usage["prompt_tokens"],
    "output_tokens": usage["completion_tokens"],
    "seconds": round(time.time() - start, 1),
  }


if __name__ == "__main__":
  input_path = sys.argv[1] if len(sys.argv) > 1 else "../testdata/sample.txt"
  output_path = sys.argv[2] if len(sys.argv) > 2 else "summary.json"
  with open(input_path) as f:
    result = summarize(f.read())
  with open(output_path, "w") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
  print(result["summary"])
  print(f"\ninput_tokens={result['input_tokens']} output_tokens={result['output_tokens']} seconds={result['seconds']}")
  print(f"written to {output_path}")
