"""
/donate — Donation handler for the Malda College Bot.

Shows a bilingual (English + Bengali) donation appeal with two payment
methods accessible via inline buttons:

  1. UPI Payment  — QR code image + copy-able UPI ID
  2. Crypto       — network selector → wallet address + copy button + warning

Wallet / UPI details are kept in module-level constants so the admin can
update them in one place without touching the handler logic.

QR code image:  data/upi_qr.jpg
  Replace this file with the real QR image when ready.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logger = logging.getLogger("malda_bot.donate")

router = Router(name="donate")

# ---------------------------------------------------------------------------
# Donation constants — edit these when details change
# ---------------------------------------------------------------------------

UPI_ID = "iyyy4d@ptaxis"

# EVM wallet — used for all three USDT networks (BEP20, Polygon, Ethereum)
WALLET_ADDRESS = "0xaE2C19a119d212799D60c3011294316dfa69d239"

# Litecoin has its own separate address
LTC_WALLET_ADDRESS = "ltc1qjuk66rlld22j7rsqjjr8edejkgjx7395s9ct35"

# QR code image path — relative to the project root
_QR_PATH = Path(__file__).parent.parent / "data" / "upi_qr.jpg"

# Network display labels mapped to callback data keys
CRYPTO_NETWORKS: dict[str, str] = {
    "usdt_bep20": "USDT (BEP20 — Binance Smart Chain)",
    "usdt_pol":   "USDT (Polygon)",
    "usdt_eth":   "USDT (Ethereum / ERC20)",
    "ltc":        "Litecoin (LTC)",
}

# Networks that share the EVM wallet address above
EVM_NETWORKS = {"usdt_bep20", "usdt_pol", "usdt_eth"}

# ---------------------------------------------------------------------------
# Text constants
# ---------------------------------------------------------------------------

INTRO_TEXT = (
    "🎓 <b>Support My Education</b>\n"
    "🎓 <b>আমার শিক্ষায় সহায়তা করুন</b>\n\n"
    "I am a BCA student pursuing my degree at Malda College. "
    "To complete my coursework, projects, and practical assignments, "
    "I urgently need a laptop — any amount you can contribute will "
    "make a real difference. Thank you from the bottom of my heart. 🙏\n\n"
    "আমি মালদা কলেজে BCA পড়াশোনা করছি। "
    "কোর্সওয়ার্ক, প্রজেক্ট এবং প্র্যাক্টিক্যাল অ্যাসাইনমেন্ট সম্পন্ন করতে "
    "আমার একটি ল্যাপটপ অত্যন্ত প্রয়োজন — আপনি যতটুকু সাহায্য করতে পারেন "
    "তা আমার জন্য অনেক বড় পার্থক্য তৈরি করবে। আন্তরিক ধন্যবাদ। 🙏\n\n"
    "Choose a payment method below:"
)

UPI_CAPTION = (
    "💳 <b>UPI Payment</b>\n\n"
    "Scan the QR code above, or use the UPI ID directly:\n\n"
    "<code>{upi_id}</code>\n\n"
    "Tap the code to copy, then paste it into any UPI app "
    "(PhonePe, GPay, Paytm, BHIM, etc.).\n\n"
    "🇮🇳 UPI পেমেন্টের জন্য উপরের QR কোড স্ক্যান করুন অথবা "
    "সরাসরি UPI ID ব্যবহার করুন।"
)

CRYPTO_NETWORK_PROMPT = (
    "🪙 <b>Crypto Donation</b>\n\n"
    "Select the network you want to send on:"
)

CRYPTO_ADDRESS_TEXT = (
    "🪙 <b>Crypto Donation — {network_label}</b>\n\n"
    "Wallet address:\n"
    "<code>{address}</code>\n\n"
    "Tap the address to copy it, then send your donation.\n\n"
    "⚠️ <b>Important / গুরুত্বপূর্ণ:</b>\n"
    "Only send <b>USDT</b> on BEP20 (BSC), Polygon, or Ethereum to this address — "
    "do <b>not</b> send native BNB, POL, or ETH. "
    "Sending on the wrong network may result in <b>permanent loss of funds.</b>\n"
    "এই ঠিকানায় শুধুমাত্র <b>USDT</b> (BEP20, Polygon, বা Ethereum) পাঠান — "
    "নেটিভ BNB, POL বা ETH পাঠাবেন না। "
    "ভুল নেটওয়ার্কে পাঠালে ফান্ড চিরতরে হারিয়ে যেতে পারে।"
)

LTC_ADDRESS_TEXT = (
    "🪙 <b>Crypto Donation — Litecoin (LTC)</b>\n\n"
    "Wallet address:\n"
    "<code>{address}</code>\n\n"
    "Tap the address to copy it, then send your donation.\n\n"
    "ℹ️ Make sure you are sending on the <b>Litecoin (LTC)</b> network only.\n"
    "নিশ্চিত করুন আপনি শুধুমাত্র <b>Litecoin (LTC)</b> নেটওয়ার্কে পাঠাচ্ছেন।"
)

# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 UPI Payment", callback_data="donate:upi"),
            InlineKeyboardButton(text="🪙 Crypto",      callback_data="donate:crypto"),
        ]
    ])


def _network_keyboard() -> InlineKeyboardMarkup:
    """One button per supported crypto network, plus a Back button."""
    buttons = [
        [InlineKeyboardButton(
            text=label,
            callback_data=f"donate:net:{key}",
        )]
        for key, label in CRYPTO_NETWORKS.items()
    ]
    buttons.append([
        InlineKeyboardButton(text="⬅️ Back", callback_data="donate:back")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _copy_upi_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy UPI ID", callback_data="donate:copy_upi")],
        [InlineKeyboardButton(text="⬅️ Back",        callback_data="donate:back")],
    ])


def _copy_address_keyboard(net_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy Address",  callback_data=f"donate:copy_addr:{net_key}")],
        [InlineKeyboardButton(text="⬅️ Choose network", callback_data="donate:crypto")],
        [InlineKeyboardButton(text="⬅️ Back to donate", callback_data="donate:back")],
    ])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_qr() -> bytes | None:
    """Read the QR image from disk; return None if missing."""
    if _QR_PATH.exists():
        return _QR_PATH.read_bytes()
    logger.warning("QR image not found at %s", _QR_PATH)
    return None


# ---------------------------------------------------------------------------
# /donate entry point
# ---------------------------------------------------------------------------

@router.message(Command("donate"))
async def cmd_donate(message: Message) -> None:
    await message.answer(INTRO_TEXT, reply_markup=_main_keyboard())


# ---------------------------------------------------------------------------
# Inline button callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "donate:upi")
async def cb_upi(callback: CallbackQuery) -> None:
    """Show the UPI QR code + UPI ID."""
    await callback.answer()

    caption = UPI_CAPTION.format(upi_id=UPI_ID)
    qr_bytes = _load_qr()

    if qr_bytes:
        await callback.message.answer_photo(
            photo=BufferedInputFile(qr_bytes, filename="upi_qr.jpg"),
            caption=caption,
            reply_markup=_copy_upi_keyboard(),
        )
    else:
        # Fallback if QR image is not yet on disk
        await callback.message.answer(
            "💳 <b>UPI Payment</b>\n\n"
            "⚠️ QR code image is not yet available. "
            "Please use the UPI ID directly:\n\n"
            f"<code>{UPI_ID}</code>\n\n"
            "Tap to copy, then open any UPI app.",
            reply_markup=_copy_upi_keyboard(),
        )


@router.callback_query(F.data == "donate:copy_upi")
async def cb_copy_upi(callback: CallbackQuery) -> None:
    """Re-send the UPI ID as plain text so it's easy to long-press-copy."""
    await callback.answer("UPI ID sent! Tap the code to copy it.", show_alert=False)
    await callback.message.answer(
        f"💳 UPI ID:\n\n<code>{UPI_ID}</code>\n\n"
        "Tap the ID above to copy it."
    )


@router.callback_query(F.data == "donate:crypto")
async def cb_crypto(callback: CallbackQuery) -> None:
    """Show the network selector."""
    await callback.answer()
    await callback.message.answer(
        CRYPTO_NETWORK_PROMPT,
        reply_markup=_network_keyboard(),
    )


@router.callback_query(F.data.startswith("donate:net:"))
async def cb_network_selected(callback: CallbackQuery) -> None:
    """Show wallet address for the selected network."""
    net_key = callback.data.split("donate:net:", 1)[1]
    network_label = CRYPTO_NETWORKS.get(net_key, net_key)

    await callback.answer()

    if net_key == "ltc":
        text = LTC_ADDRESS_TEXT.format(address=LTC_WALLET_ADDRESS)
    else:
        text = CRYPTO_ADDRESS_TEXT.format(
            network_label=network_label,
            address=WALLET_ADDRESS,
        )

    await callback.message.answer(
        text,
        reply_markup=_copy_address_keyboard(net_key),
    )


@router.callback_query(F.data.startswith("donate:copy_addr:"))
async def cb_copy_address(callback: CallbackQuery) -> None:
    """Re-send the wallet address as plain text for easy copying."""
    net_key = callback.data.split("donate:copy_addr:", 1)[1]
    address = LTC_WALLET_ADDRESS if net_key == "ltc" else WALLET_ADDRESS
    await callback.answer("Address sent! Tap the code to copy it.", show_alert=False)
    await callback.message.answer(
        f"🪙 Wallet address:\n\n<code>{address}</code>\n\n"
        "Tap the address above to copy it."
    )


@router.callback_query(F.data == "donate:back")
async def cb_back(callback: CallbackQuery) -> None:
    """Return to the main donation message."""
    await callback.answer()
    await callback.message.answer(INTRO_TEXT, reply_markup=_main_keyboard())
