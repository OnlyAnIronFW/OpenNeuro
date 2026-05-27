#!/usr/bin/env python
"""S1 MiniCPM-o 4.5 LoRA 微调脚本 (unsloth)

用法:
  python scripts/finetune_s1.py --data data/training/s1_sharegpt.json --output ./s1_finetuned

前置:
  pip install unsloth transformers datasets accelerate peft
"""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="ShareGPT格式训练数据")
    parser.add_argument("--output", default="./s1_finetuned", help="输出目录")
    parser.add_argument("--model", default="openbmb/MiniCPM-o-4_5", help="基座模型")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lora_r", type=int, default=16)
    parser.add_argument("--lora_alpha", type=int, default=32)
    args = parser.parse_args()

    # 验证数据
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    print(f"加载 {len(data)} 条训练样本")

    # ── 微调 (使用 unsloth) ──
    try:
        from unsloth import FastLanguageModel
        import torch

        print(f"加载模型: {args.model}")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.model,
            max_seq_length=2048,
            load_in_4bit=True,
            fast_inference=False,
        )

        # LoRA 配置
        model = FastLanguageModel.get_peft_model(
            model,
            r=args.lora_r,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                           "gate_proj", "up_proj", "down_proj"],
            lora_alpha=args.lora_alpha,
            lora_dropout=0.05,
            bias="none",
            use_gradient_checkpointing="unsloth",
        )

        # 格式化训练数据
        def format_fn(examples):
            texts = []
            for conv in examples["conversations"]:
                text = ""
                for turn in conv:
                    role = "user" if turn["from"] == "human" else "assistant"
                    text += f"<|{role}|>\n{turn['value']}\n"
                text += "<|assistant|>\n"
                texts.append(text)
            return {"text": texts}

        from datasets import Dataset
        dataset = Dataset.from_list(data)
        dataset = dataset.map(format_fn, batched=True)

        from transformers import TrainingArguments
        from trl import SFTTrainer

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            dataset_text_field="text",
            max_seq_length=2048,
            args=TrainingArguments(
                per_device_train_batch_size=2,
                gradient_accumulation_steps=4,
                warmup_steps=5,
                num_train_epochs=args.epochs,
                learning_rate=2e-4,
                fp16=not torch.cuda.is_bf16_supported(),
                bf16=torch.cuda.is_bf16_supported(),
                logging_steps=1,
                optim="adamw_8bit",
                output_dir=args.output,
            ),
        )

        print("开始训练...")
        trainer.train()

        # 保存
        model.save_pretrained(args.output)
        tokenizer.save_pretrained(args.output)
        print(f"模型保存到: {args.output}")

    except ImportError as e:
        print(f"[跳过微调] 缺少依赖: {e}")
        print("手动运行:")
        print(f"  1. 安装: pip install unsloth transformers datasets accelerate peft trl")
        print(f"  2. 运行: python scripts/finetune_s1.py --data {args.data}")
        print(f"  数据已就绪: {args.data} ({len(data)} 条样本)")


if __name__ == "__main__":
    main()
