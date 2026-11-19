from __future__ import annotations

from zoro_rag.progress import progress

class Qwen3Reranker:
    """Official Qwen3 yes/no likelihood scoring, without text generation.

    Reference: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B#using-transformers
    """

    def __init__(self, model_name: str, device: str = "auto", cache_dir: str | None = None,
                 batch_size: int = 4, max_length: int = 2048):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch, self.batch_size, self.max_length = torch, batch_size, max_length
        torch.set_num_threads(4)
        selected = ("cuda:0" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir, padding_side="left")
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name, cache_dir=cache_dir,
                torch_dtype=torch.float16 if selected.startswith("cuda") else torch.float32,
                attn_implementation="sdpa",
            ).to(selected).eval()
        except Exception as exc:
            raise RuntimeError(f"Unable to load official reranker {model_name} on {selected}: {exc}. "
                               "Reduce reranker_batch_size or explicitly disable reranker_enabled.") from exc
        self.no_id = self.tokenizer.convert_tokens_to_ids("no")
        self.yes_id = self.tokenizer.convert_tokens_to_ids("yes")
        prefix = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and '
                  'the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
        suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
        self.prefix = self.tokenizer.encode(prefix, add_special_tokens=False)
        self.suffix = self.tokenizer.encode(suffix, add_special_tokens=False)

    def rerank(self, query, candidates):
        task = "Retrieve documented process knowledge, variable definitions, causes and safety constraints relevant to the alarm context."
        with self.torch.inference_mode():
            for start in progress(range(0, len(candidates), self.batch_size), desc="Qwen 리랭커", unit="batch"):
                batch = candidates[start:start + self.batch_size]
                pairs = [f"<Instruct>: {task}\n<Query>: {query}\n<Document>: {item.chunk.text}" for item in batch]
                inputs = self.tokenizer(pairs, padding=False, truncation=True, add_special_tokens=False,
                                        max_length=self.max_length - len(self.prefix) - len(self.suffix))
                inputs["input_ids"] = [self.prefix + ids + self.suffix for ids in inputs["input_ids"]]
                # Rebuild attention masks after inserting the official prefix and suffix.
                inputs = self.tokenizer.pad({"input_ids": inputs["input_ids"]}, padding=True, return_tensors="pt")
                inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
                # Only materialize the final token vocabulary logits.
                logits = self.model(**inputs, logits_to_keep=1).logits[:, -1, :]
                scores = self.torch.softmax(logits[:, [self.no_id, self.yes_id]].float(), dim=-1)[:, 1].tolist()
                for item, score in zip(batch, scores):
                    item.reranker_score = float(score)
        return sorted(candidates, key=lambda item: item.reranker_score if item.reranker_score is not None else float("-inf"), reverse=True)
