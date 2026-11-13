"""Conservative, auditable guard. No semantic DLP guarantees are claimed."""
from __future__ import annotations

import re
import unicodedata
import json
import os
import secrets as random_secrets
from decimal import Decimal
from pathlib import Path

from .core import digest, read_json, write_json

GENERIC_HELP = {
    "translation": "Give a generic checklist for accurate Russian-Chinese technical translation. Do not use examples from real documents.",
    "contracts": "Give a generic method to compare a contract against an internal policy and document justified revisions. No case-specific examples.",
    "aggregation": "Give a generic method for reconciling heterogeneous tables and tracking source provenance, ambiguity and missing values.",
    "presentation": "Give a generic structure for a concise management presentation with claims supported by source documents.",
    "html": "Explain generic techniques for an offline HTML dashboard using Konva charts and Lucide icons, including monthly, quarterly and annual aggregation.",
    "archive": "Give a generic method for classifying procurement documents, grouping versions and identifying ambiguous duplicates.",
}


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def redact(text: str, secrets: list[dict]):
    mapping = {}
    for i, item in enumerate(sorted(secrets, key=lambda s: len(s["value"]), reverse=True)):
        token = f"[PRIVATE_{i+1}]"
        pattern = re.compile(re.escape(item["value"]), re.IGNORECASE)
        if pattern.search(text):
            mapping[token] = item["value"]
            text = pattern.sub(lambda _: token, text)
    return text, mapping


def prepare_advice(mode: str, arguments: dict, secrets: list[dict], policy="generic_only", vault_dir=None,
                   semantic_sensitive=False):
    if mode == "local":
        return {"allowed": False, "reason": "advisor_disabled"}
    requested = str(arguments.get("question", ""))
    if mode == "self_guard":
        return {"allowed": True, "question": requested, "policy": "model_self_guard"}
    if policy == "pseudonymize_affine" and not semantic_sensitive:
        if vault_dir is None:
            raise ValueError("Local privacy vault directory required")
        return protect_question(requested, Path(vault_dir))
    redacted, mapping = redact(requested, secrets)
    topic = arguments.get("topic")
    if topic not in GENERIC_HELP:
        return {"allowed": False, "reason": "unknown_generic_topic"}
    # Never forward free text: paraphrases can disclose semantic corporate secrets.
    return {"allowed": True, "question": GENERIC_HELP[topic], "policy": "generic_only",
            "redacted_preview": redacted, "redaction_count": len(mapping),
            "free_text_forwarded": False,
            "semantic_fallback": semantic_sensitive}


# Local rule-based recognizers. No judge annotations are used for detection.
# This baseline can miss inflected names, unseen identifiers and contextual secrets.
ENTITY = re.compile(
    r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
    r'|(?:ООО|АО|ПАО|ЗАО|ИП)\s+(?:[«"][^»"\n]+[»"]|[А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+){0,2})'
    r'|[\u4e00-\u9fff]{2,}(?:有限公司|公司)'
    r'|(?:联系人|姓名|负责人)[:：\s]+[\u4e00-\u9fff]{2,4}'
    r'|(?<![\u4e00-\u9fff])[陈王李张刘赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文][\u4e00-\u9fff]{1,2}(?![\u4e00-\u9fff])'
    r'|(?<!\w)[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?'
    r'|(?<!\w)[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?'
    r'|\b(?:sk|api)[-_][A-Za-z0-9_-]{8,}\b'
    r'|\b[A-Za-zА-ЯЁ]{1,8}-\d[\w-]*\b'
)
NUMBER = re.compile(r'(?<![\w])[-+]?\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?(?![\w])')
TOKEN = re.compile(r'\[ENTITY_[A-F0-9]+\]|\[NUM_[A-F0-9]+\|[-+]?\d+(?:\.\d+)?\]')


def vault_key(folder: Path):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "key.bin"
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(AESGCM.generate_key(bit_length=256))
            path.chmod(0o600)
        except FileExistsError:
            pass
    return path.read_bytes()


def encrypt_record(folder, identifier, value):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ciphertext = AESGCM(vault_key(folder)).encrypt(nonce, json.dumps(value, ensure_ascii=False).encode(), identifier.encode())
    write_json(folder / f"{identifier}.json", {"nonce": nonce.hex(), "ciphertext": ciphertext.hex()})


def decrypt_record(folder, identifier):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    record = read_json(folder / f"{identifier}.json")
    plaintext = AESGCM(vault_key(folder)).decrypt(bytes.fromhex(record["nonce"]), bytes.fromhex(record["ciphertext"]), identifier.encode())
    return json.loads(plaintext)


def protect_question(question: str, folder: Path):
    identifier = digest({"text": question, "policy": "pseudonymize_affine-v2"})
    vault_file = folder / f"{identifier}.json"
    if vault_file.exists():
        record = decrypt_record(folder, identifier)
    else:
        key = vault_key(folder)
        import hmac
        # Deterministic within a run, unlinkable across independently keyed runs.
        a = Decimal(11+int.from_bytes(hmac.digest(key, b"scale", "sha256")[:4], "big") % 989)
        b = Decimal(1001+int.from_bytes(hmac.digest(key, b"offset", "sha256")[:4], "big") % 999983)
        replacements, values = {}, {}
        def entity(match):
            original = match.group()
            code = hmac.digest(key, ("entity:"+normalized(original)).encode(), "sha256").hex()[:16].upper()
            token = f"[ENTITY_{code}]"
            replacements[token] = original
            return token
        pieces, last = [], 0
        # Protect recognisable entities first; their numeric IDs must not be transformed again.
        for match in ENTITY.finditer(question):
            pieces.append(question[last:match.start()])
            pieces.append(entity(match))
            last = match.end()
        pieces.append(question[last:])
        text = "".join(pieces)
        def number(match):
            original = match.group()
            x = Decimal(re.sub(r'[ \u00a0\u202f]', '', original).replace(',', '.'))
            y = a*x+b
            code = hmac.digest(key, ("number:"+str(x)).encode(), "sha256").hex()[:16].upper()
            token = f"[NUM_{code}|{format(y, 'f')}]"
            replacements[token] = original
            values[token] = {"x": str(x), "y": str(y)}
            return token
        # Do not match numbers inside protected tokens.
        protected_parts = re.split(r'(\[ENTITY_[A-F0-9]+\])', text)
        text = "".join(p if p.startswith("[ENTITY_") else NUMBER.sub(number, p) for p in protected_parts)
        record = {"question": text, "replacements": replacements, "numbers": values, "a": str(a), "b": str(b)}
        encrypt_record(folder, identifier, record)
    return {"allowed": True, "question": record["question"], "vault_id": identifier,
            "policy": "pseudonymize_affine", "entity_count": sum(k.startswith("[ENTITY_") for k in record["replacements"]),
            "number_count": len(record["numbers"]), "free_text_forwarded": True,
            "advisor_instruction": "Entity tokens are pseudonyms. NUM tokens contain affine-transformed numbers using one unknown positive scale and offset. Preserve tokens exactly. Ordering is preserved; sums, ratios, percentages and products of transformed numbers are NOT valid original quantities. Give symbolic formulas using original tokens; do not invent new NUM tokens. Confidential technical meaning may still be present; this is an experimental privacy mechanism, not encryption of meaning."}


def restore_advice(text: str, folder: Path, identifier: str):
    record = decrypt_record(folder, identifier)
    # Only known complete tokens are reversible. Never invert arbitrary model-generated numbers.
    return TOKEN.sub(lambda m: record["replacements"].get(m.group(), m.group()), text)
