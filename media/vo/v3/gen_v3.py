"""Generate voiceover v3 with Breeze TTS 2: beat 0 voice-designs the narrator,
beats 1-8 use voice direction cloned from beat 0 for a consistent identity."""

import json
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, "/home/azidan/AQL/AI/breeze-tts")

from breeze_infer.runtime import (
    load_runtime,
    resolve_device,
    set_all_seeds,
    update_generation_config_for_breeze,
)
from breeze_infer.templates import get_template, prepare_inputs
from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig

MODEL = Path("/home/azidan/AQL/AI/breeze-tts-2")
BEATS = json.load(open("/home/azidan/AQL/AI/MORTGPU/media/vo/beats.json"))
OUT = Path("/home/azidan/AQL/AI/MORTGPU/media/vo/v3")
OUT.mkdir(parents=True, exist_ok=True)

IDENTITY = "a calm, intriguing tech narrator with a clear, confident voice"


def split_entry(entry):
    raw = entry[1]
    if raw.startswith("["):
        tag, _, text = raw[1:].partition("]")
        return tag.strip(), text.strip()
    return "neutral", raw.strip()


def generate(runtime, tokenizer, audio_tokenizer, model, text, instruction, output, ref=None):
    request = {"id": output.stem, "text": text, "instruction": instruction, "speaker": "S0"}
    template_name = "tts_instruction"
    if ref is not None:
        request["ref_audio_path"] = str(ref)
        request["ref_text"] = REF_TEXT
        template_name = "ref_edit_tata"
    set_all_seeds(42)
    inputs = prepare_inputs(
        tokenizer, audio_tokenizer, model, [request], get_template(template_name),
        guidance_scale=4.0, guidance_scale_ref=None, guidance_scale_ins=None,
    )
    with sf.SoundFile(output, mode="w", samplerate=runtime.sample_rate, channels=1, subtype="PCM_16") as f:
        for chunk in runtime.iter_audio_chunks(inputs, request_id=request["id"]):
            f.write(chunk.audio)
    print(f"saved {output}", flush=True)


tokenizer, model, audio_tokenizer = load_runtime(
    MODEL, device=resolve_device(), attn_implementation="eager"
)
update_generation_config_for_breeze(model)

runtime = FastBreezeStreamingRuntime(
    model, audio_tokenizer,
    FastStreamingConfig(max_new_tokens=1500, max_seq_len=2048, repetition_penalty=1.1),
    tokenizer=tokenizer,
)

REF_TEXT = split_entry(BEATS[0])[1]
for i, entry in enumerate(BEATS):
    tag, text = split_entry(entry)
    out = OUT / f"vo_{entry[0]}.wav"
    if i == 0:
        print(f"[{entry[0]}] voice design: {tag!r}", flush=True)
        generate(runtime, tokenizer, audio_tokenizer, model, text, IDENTITY.capitalize() + ".", out)
    else:
        print(f"[{entry[0]}] voice direction {tag!r} (cloned from vo_00)", flush=True)
        generate(runtime, tokenizer, audio_tokenizer, model, text,
                 f"{tag}; keep the same narrator voice: {IDENTITY}.", out, ref=OUT / "vo_00.wav")

print("done: all beats")
