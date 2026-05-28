@echo off
cd /d F:\OpenNeuro
set "HF_DIR=%USERPROFILE%\.cache\huggingface\hub\models--openbmb--MiniCPM-o-4_5"

REM ────────────────────────────────────────
REM 1. 检查/下载模型 (仅 TTS 部分, ~3GB)
REM ────────────────────────────────────────
if exist "%HF_DIR%\snapshots" (
    echo [1/3] Model already cached
) else (
    echo [1/3] Downloading MiniCPM-o TTS weights (~3GB, one-time)...
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
        preprocessor_config.json ^
        --local-dir "%HF_DIR%/snapshots/main"
    if %errorlevel% neq 0 (
        echo FAILED to download model. Check network.
        pause
        exit /b 1
    )
)

REM ────────────────────────────────────────
REM 2. 预处理数据集
REM ────────────────────────────────────────
echo.
echo [2/3] Preparing dataset...
python scripts/prepare_voice_dataset.py --format jsonl

REM ────────────────────────────────────────
REM 3. 启动训练
REM ────────────────────────────────────────
echo.
echo [3/3] Starting training...
echo ============================================================
echo   Roxy TTS Fine-Tuning
echo   Model:  MiniCPM-o-4_5 TTS decoder (300M params)
echo   GPU:    RTX 2080 Ti (11.8 GB)
echo   Data:   239 samples, ~20 min
echo   Expected: 30-60 min
echo ============================================================
echo.

python scripts/train_voice_roxy.py

pause
