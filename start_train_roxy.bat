@echo off
cd /d F:\OpenNeuro

set "HF_SNAPSHOTS=%USERPROFILE%\.cache\huggingface\hub\models--openbmb--MiniCPM-o-4_5\snapshots"

REM ────────────────────────────────────────
REM 1. Download TTS weights (~3GB, one-time)
REM ────────────────────────────────────────
if exist "%HF_SNAPSHOTS%\*\model-00004-of-00004.safetensors" (
    echo [1/3] TTS model already cached
) else (
    echo [1/3] Downloading TTS shard + config (~3GB, one-time)...
    huggingface-cli download openbmb/MiniCPM-o-4_5 ^
        model-00004-of-00004.safetensors ^
        model.safetensors.index.json ^
        config.json ^
        configuration_minicpmo.py ^
        modeling_minicpmo.py ^
        modeling_navit_siglip.py ^
        processing_minicpmo.py ^
        tokenization_minicpmo_fast.py ^
        utils.py ^
        tokenizer_config.json ^
        tokenizer.json ^
        special_tokens_map.json ^
        vocab.json ^
        merges.txt ^
        added_tokens.json ^
        generation_config.json ^
        preprocessor_config.json

    if %errorlevel% neq 0 (
        echo.
        echo DOWNLOAD FAILED. Check network or use: set HF_ENDPOINT=https://hf-mirror.com
        pause
        exit /b 1
    )
    echo   Done.
)

REM ────────────────────────────────────────
REM 2. Prepare dataset
REM ────────────────────────────────────────
echo.
echo [2/3] Preparing dataset...
python scripts/prepare_voice_dataset.py --format jsonl

REM ────────────────────────────────────────
REM 3. Train
REM ────────────────────────────────────────
echo.
echo [3/3] Starting training...
echo ============================================================
echo   Roxy TTS Fine-Tuning
echo   Model:  MiniCPMTTS ^(300M params, LoRA^)
echo   GPU:    RTX 2080 Ti ^(11.8 GB^)
echo   Time:   30-60 min
echo ============================================================
echo.
python scripts/train_voice_roxy.py
pause
