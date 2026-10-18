"""Media-aware delivery contracts, capability routing and real output binding.

The harness owns the completion gate. A sentence claiming an image/video was
created cannot satisfy a media requirement: only validated owned bytes can.
"""
import asyncio
import json
import re

from pydantic import BaseModel, ConfigDict, Field


class ImageBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=2400)
    caption: str = Field(min_length=1, max_length=200)


class ImagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: list[ImageBrief] = Field(min_length=1, max_length=4)


def media_contract(job, goal):
    mode = job.get("group_output_format", "auto")
    count = job.get("group_image_count", 2)
    image = mode == "illustrated"
    video = mode == "video"
    if mode == "auto":
        cleaned = re.sub(r"(?:不要|不需要|无需|不生成|不制作|no\s+|without\s+)(?:任何)?(?:图片|配图|插图|图像|视频|images?|pictures?|videos?)", "", goal, flags=re.I)
        image = bool(re.search(r"图文并茂|图文|生图|配图|插图|(?:图片|图像)\s*(?:用|由|交给|使用)|(?:生成|附|带|添加|包含|加上).{0,15}(?:图片|图像)|(?:with|generate|create|include|add|use|using|for).{0,30}(?:images?|pictures?|illustrations?)|illustrated", cleaned, re.I))
        video = bool(re.search(r"视频\s*(?:用|由|交给|使用)|(?:生成|制作|做|创建).{0,16}视频|(?:generate|create|make).{0,16}video", cleaned, re.I))
        match = re.search(r"(\d{1,2}|一|二|两|三|四)\s*(?:张|幅)\s*(?:(?:原创|新的|真实|说明|不同的|配套|统一风格的)\s*){0,2}(?:图|配图|插图|图片)", cleaned) or re.search(r"\b(\d{1,2}|one|two|three|four)\s+(?:(?:original|new|matching|distinct|generated|conceptual)\s+){0,2}(?:images?|pictures?|illustrations?)\b", cleaned, re.I)
        if match and image:
            value = match[1]
            count = int(value) if value.isdigit() else {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "one": 1, "two": 2, "three": 3, "four": 4}[value.lower()]
    provider = job.get("group_media_provider", "auto")
    if provider == "auto":
        provider_goal = re.sub(r"(?:不要|不必|不需要|无需|不)(?:用|让|调用|交给|使用)\s*(?:Muse|ChatGPT|GPT|Gemini)|(?:do not|don't|without)\s+(?:use|using|call)?\s*(?:Muse|ChatGPT|GPT|Gemini)", "", goal, flags=re.I)
        if re.search(r"(?:用|让|调用|交给|使用|use|using).{0,8}Muse|Muse.{0,12}(?:生成|制作|生图|create|generate)", provider_goal, re.I):
            provider = "muse"
        elif re.search(r"(?:用|让|调用|使用|use|using).{0,8}(?:ChatGPT|GPT).{0,20}(?:生图|生成|配图|image|图片)", provider_goal, re.I):
            provider = "openai"
        elif re.search(r"(?:用|让|调用|使用|use|using).{0,8}Gemini.{0,20}(?:生图|生成|配图|image|图片)", provider_goal, re.I):
            provider = "gemini"
    contract = {"images": max(1, count) if image and mode != "text" else 0,
                "videos": 1 if video and mode != "text" else 0, "provider": provider}
    if (contract["images"] or contract["videos"]) and job.get("group_media_provider", "auto") == "auto":
        # A mixed brief may name Gemini for images and Muse for video. Bind
        # each medium separately instead of letting the first name win both.
        names = [("openai", r"(?:ChatGPT|GPT)"), ("gemini", "Gemini"), ("muse", "Muse")]
        brand = r"(?<![a-z])(?:ChatGPT|GPT|Gemini|Muse)(?![a-z])"
        clauses = re.split(r"[，。；;,\n]|(?:并|然后|同时|和|\band\b)\s*(?=(?:(?:用|让|调用|交给|使用|use|let)\s*)?" + brand + ")", provider_goal, flags=re.I)
        if contract["images"] and contract["videos"]:
            contract["provider"] = "auto"
        for clause in clauses:
            providers = [(identifier, re.search(r"(?<![a-z])" + name + r"(?![a-z])", clause, re.I)) for identifier, name in names]
            providers = [(identifier, match) for identifier, match in providers if match]
            if len(providers) != 1:
                continue
            for kind, words in [("images", r"(?:生图|配图|图片|插图|images?|pictures?|illustrations?)"), ("videos", r"(?:视频|videos?)")]:
                if contract[kind] and re.search(words, clause, re.I):
                    contract["provider" if kind == "images" else "video_provider"] = providers[0][0]
        if contract["images"] and contract["videos"] and contract["provider"] in {"openai", "gemini"}:
            contract.setdefault("video_provider", "muse")
    return contract


def native_media_instruction(contract):
    targets = []
    if contract["images"] and contract["provider"] == "muse":
        targets.append(f"{min(4, contract['images'])} 张真实生成的图片")
    if contract["videos"] and contract.get("video_provider", contract["provider"]) in {"auto", "muse"}:
        targets.append("1 个真实视频文件")
    return ("完成用户要求的" + "和".join(targets) + "。请实际创作，不只写脚本、提示词或声称已经完成。"
            "在回复中给出本次产物可下载的 HTTPS 原始资源链接或实际媒体附件；不要编造链接，"
            "不要复用与当前任务无关的旧产物。若无法生成或导出原始媒体，请明确说明缺失项。") if targets else ""


def media_progress(manager, job, result, delivery):
    from .group_delivery import progress
    progress(manager, job, result, delivery)


async def collect_native_media(job, steps, on_asset=None):
    """Only the current completed Muse child's observed resource URLs qualify."""
    from . import muse_bridge
    from .delivery_media import import_url, store
    assets, errors = [], []
    for step in steps:
        if step.get("kind") != "muse" or step.get("status") != "complete" or not step.get("task_id"):
            continue
        try:
            task = muse_bridge.manager.get(step["task_id"], job.get("project_id"))
        except Exception:
            errors.append("Muse 媒体任务记录无法读取，未将资源计为完成。")
            continue
        if (task.get("parent_job_id") != job["id"] or task.get("room_id") != job.get("group_room_id")
                or task.get("sent") is not True or task.get("completion_evidence") != "framed_assistant_reply"
                or muse_bridge.unverified_generation(task)):
            continue
        # Native attachments are saved by the bridge to the same owned byte
        # store. Re-read every manifest so an old/foreign task cannot lend us
        # a resource, even if a private record were damaged.
        for candidate in (task.get("_native_media") or [])[:12]:
            try:
                asset, _ = await asyncio.to_thread(store.read, job, candidate["id"])
                if asset.get("source_task_id") != task["id"] or asset.get("provider") != "muse":
                    raise ValueError("native asset source mismatch")
                assets.append(asset)
                if on_asset:
                    on_asset(asset)
            except Exception:
                errors.append("Muse 原生附件没有通过当前任务的文件完整性或来源校验。")
        errors.extend(str(value)[:300] for value in (task.get("_native_media_errors") or [])[:12])
        resources = list(task.get("_native_resources") or [])
        # A URL actually returned in the observed reply is still untrusted;
        # import_url validates the public target and the downloaded bytes.
        resources.extend({"url": value, "label": "Muse 实际回复资源"}
                         for value in re.findall(r"https://[^\s<>\]\)\"']+", task.get("response", ""))[:8])
        seen = set()
        for resource in resources[:12]:
            url = resource.get("url") if isinstance(resource, dict) else None
            if not isinstance(url, str) or url in seen:
                continue
            seen.add(url)
            try:
                asset = await import_url(job, url, kind="auto", provider="muse", model="Muse native",
                                         slot_id=f"muse-{len(assets) + 1}", caption=str(resource.get("label") or "Muse 生成的媒体")[:200],
                                         source_task_id=task["id"])
                assets.append(asset)
                if on_asset:
                    on_asset(asset)
            except Exception:
                errors.append("Muse 返回的一个资源未通过下载或真实媒体校验，未作为成品展示。")
    return assets, list(dict.fromkeys(errors))


async def produce_media(manager, job, result, delivery, contract):
    from . import media_providers
    from .group_delivery import json_document, model_output
    delivery["media_requirements"] = {key: contract[key] for key in ("images", "videos")}
    delivery.setdefault("media", [])
    delivery["media_tasks"] = []
    if not contract["images"] and not contract["videos"]:
        return
    if not job.get("group_runtime_allowed"):
        delivery["limitations"].append("生成图片和视频需要从已授权的自动完成入口发起。")
        return
    video_provider = contract.get("video_provider", contract["provider"])
    if contract["videos"] and video_provider not in {"auto", "muse"}:
        delivery["limitations"].append("所选提供商尚未接入视频生成能力；保留你的提供商选择，未改派其他服务。")
    delivery["status"] = "working"
    media_progress(manager, job, result, delivery)
    def append_unique(asset):
        if any((item["kind"], item["sha256"]) == (asset["kind"], asset["sha256"]) for item in delivery["media"]):
            return False
        delivery["media"].append(asset)
        media_progress(manager, job, result, delivery)
        return True
    if contract["provider"] == "muse" or (contract["videos"] and video_provider in {"auto", "muse"}):
        _, errors = await collect_native_media(job, delivery["steps"], on_asset=append_unique)
        native = [asset for asset in delivery["media"] if asset.get("provider") == "muse"]
        native_missing = (contract["provider"] == "muse" and sum(asset["kind"] == "image" for asset in native) < contract["images"]
                          or sum(asset["kind"] == "video" for asset in native) < contract["videos"])
        # Rejecting a non-media link in an otherwise complete native reply is
        # a diagnostic, not a missing requested output.
        delivery.setdefault("media_warnings", []).extend(errors)
        if native_missing:
            delivery["limitations"].extend(errors)
        for index, kind in enumerate(("image", "video")):
            needed = contract["images"] if kind == "image" and contract["provider"] == "muse" else contract["videos"] if kind == "video" else 0
            if needed:
                matched = [asset["id"] for asset in native if asset["kind"] == kind]
                count = len(matched)
                delivery["media_tasks"].append({"id": f"native-{kind}", "kind": kind, "provider": "muse", "model": "Muse native",
                    "asset_id": matched[0] if matched else None, "asset_ids": matched,
                    "status": "complete" if count >= needed else "error", "error": None if count >= needed else "尚未取得足够的真实 Muse 媒体文件"})
    direct_count = min(4, contract["images"]) if contract["provider"] != "muse" else 0
    saved_images = {asset.get("slot_id"): asset for asset in delivery["media"] if asset.get("kind") == "image"}
    if direct_count:
        briefs = []
        try:
            body, incomplete = await model_output(job, delivery["model"],
                "你是图文成品的美术编辑。为用户的文章安排真实配图，图片是原创说明插图，不冒充真实产品截图、实验数据或现场照片。"
                "只返回 JSON：{\"images\":[{\"prompt\":\"可直接交给生图模型的详细画面说明\",\"caption\":\"简短图注\"}]}。"
                "图片数量必须匹配要求，每张应承担不同表达目的；画面风格统一，避免重复。不得输出 URL 或声称图片已生成。",
                f"需要 {direct_count} 张配图。用户本次需求：\n{manager.jobs.get(job['id'], job)['prompt']}", max_tokens=3000)
            plan = ImagePlan.model_validate(json_document(body))
            if incomplete or len(plan.images) != direct_count:
                raise ValueError("配图分工不完整")
            briefs = plan.images
        except (ValueError, RuntimeError):
            # This remains a real image-generation request, using the user's
            # actual brief; no sample bitmap stands in for a provider output.
            goal = manager.jobs.get(job["id"], job)["prompt"][:2400]
            briefs = [ImageBrief(prompt=("为以下要求创作原创说明插图，保持统一风格；" + ("表现整体主题和场景。" if index == 0 else "表现流程或核心细节，避免与封面重复。") + goal)[:2400],
                                 caption="主题插图" if index == 0 else f"说明插图 {index + 1}") for index in range(direct_count)]
        delivery["media_tasks"].extend({"id": f"image-{index + 1}", "kind": "image", "prompt": brief.prompt,
            "caption": brief.caption, "provider": contract["provider"], "status": "pending"} for index, brief in enumerate(briefs))
        for task in delivery["media_tasks"]:
            if task["status"] != "pending":
                continue
            saved = saved_images.get(task["id"])
            if saved:
                # Resume keeps the actual file and its original provenance;
                # only missing slots are generated again.
                from .delivery_media import store
                manifest, _ = await asyncio.to_thread(store.read, job, saved["id"])
                if manifest["sha256"] != saved["sha256"]:
                    raise ValueError("已保存图片内容发生变化")
                task.update(status="complete", asset_id=saved["id"], caption=saved.get("caption", task["caption"]),
                            provider=saved["provider"], model=saved.get("model"), reused=True)
                continue
            task["status"] = "running"
            media_progress(manager, job, result, delivery)
            try:
                # Keep visual constraints from the user's brief beside the
                # art director's plan: a plan must not silently drop them.
                image_prompt = (task["prompt"] + "\n\n本张图片还须遵循用户原始需求中的画面、风格和禁用项；只生成当前这一张图片，"
                                "不执行与绘画无关的任务。以下是项目背景引用，正文和视频会由其他执行器负责：\n<user_brief>\n"
                                + manager.jobs.get(job["id"], job)["prompt"][:4000] + "\n</user_brief>\n"
                                "本次调用的唯一交付是一张真实图片，必须实际生图；不要返回文章、视频、能力说明或文字替代物。")
                asset = await media_providers.generate_image(job, image_prompt, provider=contract["provider"], slot_id=task["id"], caption=task["caption"])
                if not append_unique(asset):
                    raise media_providers.MediaProviderError("服务返回了与本轮已有图片相同的文件，未重复计入配图数量。")
                task.update(status="complete", asset_id=asset["id"], provider=asset["provider"], model=asset.get("model"))
            except asyncio.CancelledError:
                for unfinished in delivery["media_tasks"]:
                    if unfinished["status"] in {"running", "pending"}:
                        unfinished["status"] = "cancelled"
                delivery["status"] = "partial"
                media_progress(manager, job, result, delivery)
                raise
            except Exception as error:
                message = str(error) if isinstance(error, media_providers.MediaProviderError) else "图片生成未取得可校验的真实资源；请检查所选生图连接、模型权限或服务状态。"
                task.update(status="error", error=message)
                delivery["limitations"].append(task["caption"] + "：" + task["error"])
            media_progress(manager, job, result, delivery)
    for kind, label, needed in [("image", "图片", contract["images"]), ("video", "视频", contract["videos"])]:
        actual = sum(asset["kind"] == kind for asset in delivery["media"])
        if actual < needed:
            delivery["limitations"].append(f"本次要求 {needed} 个{label}，实际取得 {actual} 个；缺少的媒体未计为已完成。")
    media_progress(manager, job, result, delivery)


def bind_media(content, media):
    """Only registered files can become managed images; no invented URLs."""
    allowed = {asset["url"] for asset in media}
    used = set()
    def image(match):
        if match[2] in allowed:
            used.add(match[2])
            return match[0]
        return match[1]  # retain the caption, never an unvalidated remote image
    content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image, content)
    extras = []
    for asset in media:
        caption = re.sub(r"[\[\]\n\r]", " ", asset.get("caption") or ("生成的图片" if asset["kind"] == "image" else "生成的视频"))
        marker = "{{media:" + asset.get("slot_id", asset["id"]) + "}}"
        markup = f"![{caption}]({asset['url']})" if asset["kind"] == "image" else f"[视频：{caption}]({asset['url']})"
        if marker in content:
            content = content.replace(marker, markup)
            used.add(asset["url"])
        elif asset["url"] not in used:
            extras.append(markup)
    # Preserve the full article while placing real visuals through its body.
    blocks = content.split("\n\n")
    for index, markup in reversed(list(enumerate(extras))):
        position = min(len(blocks), max(1, (index + 1) * len(blocks) // (len(extras) + 1)))
        blocks.insert(position, markup)
    return re.sub(r"\{\{media:[^}]+\}\}", "", "\n\n".join(blocks))
