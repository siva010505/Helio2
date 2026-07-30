"""
Deep Dive Script Agent

Role:
Generates a single, long-form narrative script for ONE topic.
Format: Cold Hook → Setup the Mystery → Deep Dive → Twist/Revelation → Implication → CTA.

This replaces the CompilationAssemblerAgent (which stitched 7 unrelated shorts together).
The result is a single cohesive story arc designed for maximum viewer retention.

Inputs:
- selected topic (single topic dict)
- channel_config

Outputs:
- full_script: Complete narrative text (~1500–2000 words)
- section_titles: List of section names for chapter cards
- section_markers: List of {section_name, start_text} for visual pipeline chapter injection
- estimated_duration: Estimated spoken duration in seconds
"""

import json
import logging
from typing import Dict, Any, List

from src.db.models import PromptVersion

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Master deep-dive narrative prompt
# ──────────────────────────────────────────────────────────────────────────────
DEEP_DIVE_PROMPT = """\
You are a world-class YouTube narrator. Your job is to write a deeply engaging, \
single-topic long-form video script in the style of Vsauce, Veritasium, and Nexpo \
— mysterious, human, and gripping from the first second to the last.

NICHE: {niche}
TONE: {tone}
TARGET SPOKEN DURATION: ~{target_length_seconds} seconds (~{word_count} words at a moderate pace)

MANDATORY NARRATIVE STRUCTURE — follow this exactly:
1. COLD HOOK (First 10% of script):
   - Drop the viewer INTO the most shocking, strange, or dramatic moment of the topic — mid-action.
   - Do NOT introduce yourself. Do NOT say "Today we're going to talk about X."
   - Use vivid, sensory, immediate language. The viewer should feel like they arrived mid-scene.
   - End with a question that creates an open loop: "How did this happen? And what does it tell us about ourselves?"

2. SETUP THE MYSTERY (Next 15% of script):
   - IMMEDIATELY introduce yourself briefly as "Helio", but make it dynamic and natural to the story (e.g. "I'm Helio, and to understand why this happened, we have to go back..." or "My name is Helio, and when I first heard this case..."). Do NOT use the exact same phrase every time.
   - Provide just enough background to make the topic legible — names, dates, place, context.
   - Frame it as a puzzle the viewer will solve *with* you, not a lecture. Use first-person ("I", "we") to guide them.
   - Raise at least one additional surprising question that isn't answered yet.

3. DEEP DIVE (Middle 50% of script):
   - This is the investigation. Unpack the story layer by layer.
   - Mix: facts and evidence → human drama → expert insight → counterintuitive angle.
   - Use short rhetorical questions mid-section to re-engage: "But here's where it gets strange..."
   - Include 2–3 specific, surprising, memorable details (statistics, names, quotes) that feel like revelations.
   - Build tension progressively — each paragraph should leave the viewer slightly more unsettled or curious.

4. TWIST / REVELATION (Next 15% of script):
   - Deliver the reframe or conclusion that makes everything click.
   - This is the "Oh WOW" moment. It should genuinely surprise.
   - Connect the historical/scientific story back to modern human behaviour.

5. IMPLICATION (Final 10% of script, before CTA):
   - "What does this mean for YOU?" 
   - Make it personal and actionable. Why should the viewer care about this?
   - End on a thought-provoking, slightly unsettling note — not a neat bow.

6. CTA (Last few sentences):
   - Ask a genuine, specific question related to the topic for comments.
   - Subscribe + "next video" tease (mystery but not clickbait).

CRITICAL RULES:
- Write as if YOU are discovering this story for the first time alongside the viewer.
- NEVER use filler phrases like "In conclusion", "In today's video", "Welcome back", "Let's dive in."
- Every sentence must earn its place. Cut anything that doesn't add tension, information, or wonder.
- {commentary_style_instruction}

OUTPUT: Return a JSON object with this exact schema:
{{
  "full_script": "The complete script from cold hook to CTA...",
  "section_titles": ["Cold Hook", "<Custom Cinematic Title 1>", "<Custom Cinematic Title 2>", "<Custom Cinematic Title 3>", "<Custom Cinematic Title 4>", "Final Thought"],
  "section_start_lines": [
    {{"section": "Cold Hook", "first_words": "Exact first 5-6 words of this section..."}},
    {{"section": "<Custom Cinematic Title 1>", "first_words": "Exact first 5-6 words of this section..."}},
    {{"section": "<Custom Cinematic Title 2>", "first_words": "Exact first 5-6 words of this section..."}},
    {{"section": "<Custom Cinematic Title 3>", "first_words": "Exact first 5-6 words of this section..."}},
    {{"section": "<Custom Cinematic Title 4>", "first_words": "Exact first 5-6 words of this section..."}},
    {{"section": "Final Thought", "first_words": "Exact first 5-6 words of this section..."}}
  ]
}}
"""

# ──────────────────────────────────────────────────────────────────────────────
# Agent class
# ──────────────────────────────────────────────────────────────────────────────
class DeepDiveScriptAgent:
    """
    Generates a single-topic, long-form narrative script.
    Drop-in replacement for CompilationAssemblerAgent in the pipeline.
    """

    def __init__(self, llm_client, db_session):
        self.llm_client = llm_client
        self.db_session = db_session

    # ──────────────────────────────────────────────────────────────────────────
    # Performance addendum (same learning loop as ScriptAgent)
    # ──────────────────────────────────────────────────────────────────────────
    def _get_performance_addendum(self, channel_id: int) -> str:
        """Fetch latest EvaluationAgent-generated improvement instruction."""
        prompt_version = (
            self.db_session.query(PromptVersion)
            .filter(
                PromptVersion.channel_id == channel_id,
                PromptVersion.agent_name == "deep_dive_script_agent",
            )
            .order_by(PromptVersion.created_at.desc())
            .first()
        )
        if prompt_version and prompt_version.prompt_text:
            logger.info(
                "[DeepDiveScriptAgent] Found performance addendum for channel_id=%s (v%d).",
                channel_id,
                prompt_version.version_number,
            )
            return (
                "\n\nIMPORTANT — PERFORMANCE LEARNINGS FROM PAST VIDEOS:\n"
                + prompt_version.prompt_text
                + "\nApply these learnings while keeping the JSON output format exactly as specified above."
            )
        logger.info("[DeepDiveScriptAgent] No performance addendum found for channel_id=%s.", channel_id)
        return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Main generation method (mirrors old assemble_compilation interface)
    # ──────────────────────────────────────────────────────────────────────────
    def assemble_compilation(
        self,
        topics: List[Dict[str, Any]],
        channel_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate a deep-dive long-form script for the PRIMARY topic in `topics`.

        Signature is intentionally identical to CompilationAssemblerAgent.assemble_compilation()
        so that pipeline.py requires zero changes to the call site.

        Args:
            topics:         List of topic dicts; only the FIRST one is used.
            channel_config: Full merged channel + global config dict.

        Returns:
            Dict with keys: full_script, stories (compat shim), connective_tissue,
                            section_titles, section_markers, estimated_duration.
        """
        topic = topics[0]
        topic_text = topic.get("topic_text", "Unknown Topic")
        channel_id = topic.get("channel_id")

        logger.info("[DeepDiveScriptAgent] Generating deep-dive script for: '%s'", topic_text)

        # ── Build prompt ───────────────────────────────────────────────
        niche = channel_config.get("niche", "Psychology & human behavior")
        tone  = channel_config.get("tone", "mysterious, narrative, gripping")

        brand              = channel_config.get("brand", {})
        commentary_style   = brand.get("commentary_style", "original insight, not a dry summary")
        commentary_instruction = (
            f"Commentary style: {commentary_style}. "
            "Add at least one moment of genuine personal/editorial insight — "
            "not just 'isn't that interesting' but a specific, debatable take."
        )

        long_form_cfg       = channel_config.get("long_form", {})
        target_length       = long_form_cfg.get("target_total_length_seconds", 600)
        # 2.5 words/sec is a good measured-pace narrator rate
        word_count          = int(target_length * 2.5)

        addendum = self._get_performance_addendum(channel_id)
        system_prompt = DEEP_DIVE_PROMPT.format(
            niche=niche,
            tone=tone,
            target_length_seconds=target_length,
            word_count=word_count,
            commentary_style_instruction=commentary_instruction,
        ) + addendum

        user_prompt = (
            f"Write the complete deep-dive script for this topic:\n\n"
            f"TOPIC: {topic_text}\n"
        )
        if topic.get("description"):
            user_prompt += f"\nBACKGROUND CONTEXT:\n{topic['description']}\n"

        user_prompt += (
            "\nRemember: start COLD — drop the viewer into the most dramatic moment. "
            "No greetings, no 'today we explore'. Just the story."
        )

        # ── Call LLM ───────────────────────────────────────────────────
        try:
            result_json = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.75,
                max_tokens=3000,
            )
            logger.info("[DeepDiveScriptAgent] Script generated successfully.")
        except Exception as exc:
            logger.error("[DeepDiveScriptAgent] Script generation failed: %s", exc)
            raise

        full_script     = result_json.get("full_script", "")
        section_titles  = result_json.get("section_titles", [
            "Cold Hook", "The Mystery", "The Investigation",
            "The Revelation", "What This Means", "Final Thought",
        ])
        section_markers = result_json.get("section_start_lines", [])

        # ── Estimate spoken duration ───────────────────────────────────
        word_count_actual  = len(full_script.split())
        estimated_duration = word_count_actual / 2.5  # words per second

        if abs(estimated_duration - target_length) > target_length * 0.20:
            logger.warning(
                "[DeepDiveScriptAgent] Duration estimate %.1fs is outside ±20%% of target %ds.",
                estimated_duration, target_length,
            )

        # ── Build compatibility shim for pipeline.py ───────────────────
        # pipeline.py reads compilation_data["stories"] and
        # compilation_data["connective_tissue"] — keep those keys working.
        connective_tissue = {
            "cold_open": _extract_section(full_script, section_markers, "Cold Hook"),
            "bridges": [],          # No bridges in a single-story format
            "closing": _extract_section(full_script, section_markers, "Final Thought"),
            "chapter_titles": section_titles,
            "content_labels": ["Story"] * len(section_titles),
            "section_markers": section_markers,
        }

        stories = [{"topic": topic_text, "script": full_script}]

        logger.info(
            "[DeepDiveScriptAgent] Done. %.0f words, ~%.0fs estimated.",
            word_count_actual, estimated_duration,
        )

        return {
            "stories": stories,
            "connective_tissue": connective_tissue,
            "full_script": full_script,
            "section_titles": section_titles,
            "section_markers": section_markers,
            "estimated_duration": estimated_duration,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _extract_section(full_script: str, section_markers: list, section_name: str) -> str:
    """
    Try to extract a named section from the full_script using section_markers.
    Falls back gracefully to returning an empty string if extraction fails.
    """
    if not section_markers or not full_script:
        return ""

    # Find the marker for the requested section
    target_marker = next(
        (m for m in section_markers if m.get("section", "").lower() == section_name.lower()),
        None,
    )
    if not target_marker:
        return ""

    first_words = target_marker.get("first_words", "").strip()
    if not first_words:
        return ""

    idx = full_script.find(first_words)
    if idx == -1:
        return ""

    # Find where the NEXT section begins (so we can slice cleanly)
    next_start = len(full_script)
    for m in section_markers:
        if m.get("section", "").lower() == section_name.lower():
            continue
        fw = m.get("first_words", "").strip()
        if not fw:
            continue
        candidate_idx = full_script.find(fw, idx + 1)
        if candidate_idx != -1 and candidate_idx < next_start and candidate_idx > idx:
            next_start = candidate_idx

    return full_script[idx:next_start].strip()
