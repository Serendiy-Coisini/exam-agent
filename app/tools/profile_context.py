"""Current qualification data shared by deterministic checks and model prompts."""
from app.schemas.product_schema import UserProfilePayload


def current_qualification_profile(profile: dict, education: str, major: str, identity: str) -> dict:
    raw = profile.get("raw_profile_json")
    stored = dict(raw) if isinstance(raw, dict) else {}
    stored.update(profile)
    allowed = UserProfilePayload.model_fields.keys() - {"raw_profile_json", "filled_fields"}
    result = {key:value for key,value in stored.items() if key in allowed and value is not None}
    previous_education = stored.get("education") or stored.get("education_level") or ""
    if (str(stored.get("major") or "").strip() != major.strip()
            or str(previous_education).strip() != education.strip()):
        for key in ("major_code", "major_category", "major_discipline", "major_education"):
            result.pop(key, None)
    previous_identity = str(stored.get("identity") or "").strip()
    current_identity = str(identity or "").strip()
    if current_identity in {"往届", "往届生", "非应届毕业生"}:
        result.update(fresh_graduate_scope="not_fresh", is_fresh_graduate=False)
    elif current_identity not in {"", "不限"} and current_identity != previous_identity:
        result.pop("fresh_graduate_scope", None)
        result.pop("is_fresh_graduate", None)
        if current_identity in {"应届", "应届生", "应届毕业生", "应届高校毕业生"}:
            # A broad fresh-graduate selection does not prove a retained/current-year subtype.
            if stored.get("fresh_graduate_scope") != "not_fresh" and not previous_identity:
                result["fresh_graduate_scope"] = stored.get("fresh_graduate_scope")
            result["is_fresh_graduate"] = True
    result.update(education=education, education_level=education, major=major, identity=identity)
    return result
