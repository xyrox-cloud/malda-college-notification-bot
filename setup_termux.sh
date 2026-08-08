#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# Malda College Bot — Termux (Android) setup script
#
# Run this inside Termux after extracting malda-college-bot.zip:
#   unzip malda-college-bot.zip
#   cd malda-college-bot
#   bash setup_termux.sh
#
# It auto-detects the best install path:
#   Path A: tur-repo pre-built wheels (fastest, ~2 min)
#   Path B: compile pydantic-core from source (slower, ~15 min)
#
# After it finishes, copy .env.example to .env, fill in your
# BOT_TOKEN / ADMIN_CHAT_ID / sheet URLs, then:
#   python3 malda_bot.py
# ============================================================
set -e

echo "================================================"
echo " Malda College Bot — Termux setup"
echo "================================================"
echo ""

# --- 0. Sanity checks -------------------------------------------------------
if [ ! -f malda_bot.py ]; then
    echo "ERROR: malda_bot.py not found in the current directory."
    echo "       cd into the extracted malda-college-bot folder first."
    exit 1
fi

if [ ! -d /data/data/com.termux ]; then
    echo "ERROR: this script is meant for Termux only."
    echo "       On Linux/macOS, just run: pip install -r requirements.txt"
    exit 1
fi

# --- 1. Install base packages ----------------------------------------------
echo "[1/5] Installing base packages (python, rust, build tools)..."
pkg install -y python rust binutils patchelf make libffi openssl 2>&1 | tail -5

# --- 2. Try Path A: tur-repo (pre-built pydantic-core) --------------------
echo ""
echo "[2/5] Trying Path A: tur-repo pre-built wheels..."
if pkg install -y tur-repo 2>/dev/null; then
    pkg update -y 2>&1 | tail -3
    # Try to install pydantic-core as a system package (provides the wheel)
    if pkg install -y pydantic-core 2>/dev/null; then
        echo "  tur-repo + pydantic-core installed. Installing remaining deps..."
        pip install --no-build-isolation pydantic 2>&1 | tail -3
        pip install aiogram aiohttp beautifulsoup4 filelock 2>&1 | tail -3
        PATH_CHOSEN="A"
    else
        echo "  pydantic-core not in tur-repo yet. Falling through to Path B..."
        PATH_CHOSEN=""
    fi
else
    echo "  tur-repo unavailable. Falling through to Path B..."
    PATH_CHOSEN=""
fi

# --- 3. Path B: compile from source ---------------------------------------
if [ -z "$PATH_CHOSEN" ]; then
    echo ""
    echo "[3/5] Trying Path B: compile pydantic-core from source (~10-20 min)..."
    echo "  Setting Rust target for Termux Android..."
    export CARGO_BUILD_TARGET="aarch64-linux-android"
    export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="cc"
    # Persist these for future pip runs
    grep -q "CARGO_BUILD_TARGET" ~/.bashrc 2>/dev/null || {
        echo 'export CARGO_BUILD_TARGET="aarch64-linux-android"' >> ~/.bashrc
        echo 'export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="cc"' >> ~/.bashrc
    }
    echo "  Installing Python deps (this will compile pydantic-core — be patient)..."
    if pip install -r requirements.txt 2>&1 | tail -10; then
        PATH_CHOSEN="B"
    else
        echo ""
        echo "ERROR: Path B also failed. See the README 'Path C' section for the"
        echo "       aiogram 2.x fallback (requires code changes)."
        exit 1
    fi
fi

# --- 4. Verify install -----------------------------------------------------
echo ""
echo "[4/5] Verifying install..."
if python3 -c "import aiogram, aiohttp, bs4, filelock; print('aiogram', aiogram.__version__)" 2>&1; then
    echo "  All imports OK."
else
    echo "ERROR: import check failed. Try running pip install -r requirements.txt manually."
    exit 1
fi

# --- 5. Set up .env --------------------------------------------------------
echo ""
echo "[5/5] Setting up .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env from .env.example."
    echo "  EDIT IT NOW and fill in:"
    echo "    BOT_TOKEN, ADMIN_CHAT_ID, ODD_ROUTINE_URL, EVEN_ROUTINE_URL, CALENDAR_URL"
else
    echo "  .env already exists — leaving it untouched."
fi

# --- Done ------------------------------------------------------------------
echo ""
echo "================================================"
echo " Setup complete (Path $PATH_CHOSEN)!"
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. nano .env   (or use any editor) — fill in your secrets"
echo "  2. Acquire a wake-lock so Android doesn't kill the bot:"
echo "       termux-wake-lock"
echo "  3. Run the bot:"
echo "       python3 malda_bot.py"
echo ""
echo "To stop: Ctrl+C, then termux-wake-unlock"
echo ""
