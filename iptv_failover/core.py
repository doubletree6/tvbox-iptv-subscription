from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple


TVBOX_GROUP_ORDER = ("央视", "卫视", "其他")

QUALITY_WORDS = (
    "1080P",
    "720P",
    "2160P",
    "4K",
    "8K",
    "FHD",
    "HD",
    "SD",
    "高清",
    "超清",
    "蓝光",
    "标清",
    "频道",
)

CHINESE_SLUG_WORDS = {
    "东方": "dong-fang",
    "东南": "dong-nan",
    "兵团": "bing-tuan",
    "延边": "yan-bian",
    "深圳": "shen-zhen",
    "香港": "xiang-gang",
    "凤凰": "feng-huang",
    "资讯": "zi-xun",
    "中文": "zhong-wen",
    "金鹰": "jin-ying",
    "卡通": "ka-tong",
    "北京": "bei-jing",
    "天津": "tian-jin",
    "河北": "he-bei",
    "山西": "shan-xi",
    "内蒙古": "nei-meng-gu",
    "辽宁": "liao-ning",
    "吉林": "ji-lin",
    "黑龙江": "hei-long-jiang",
    "上海": "shang-hai",
    "江苏": "jiang-su",
    "浙江": "zhe-jiang",
    "安徽": "an-hui",
    "福建": "fu-jian",
    "江西": "jiang-xi",
    "山东": "shan-dong",
    "河南": "he-nan",
    "湖北": "hu-bei",
    "湖南": "hu-nan",
    "广东": "guang-dong",
    "广西": "guang-xi",
    "海南": "hai-nan",
    "重庆": "chong-qing",
    "四川": "si-chuan",
    "贵州": "gui-zhou",
    "云南": "yun-nan",
    "西藏": "xi-zang",
    "陕西": "shan-xi",
    "甘肃": "gan-su",
    "青海": "qing-hai",
    "宁夏": "ning-xia",
    "新疆": "xin-jiang",
    "卫视": "wei-shi",
    "少儿": "shao-er",
    "新闻": "xin-wen",
    "体育": "ti-yu",
    "电影": "dian-ying",
    "财经": "cai-jing",
    "综艺": "zong-yi",
    "中文国际": "zhong-wen-guo-ji",
}


@dataclass(frozen=True)
class ChannelEntry:
    display_name: str
    url: str
    attrs: Dict[str, str] = field(default_factory=dict)
    raw_extinf: str = ""
    order: int = 0

    @property
    def group_title(self) -> str:
        return self.attrs.get("group-title", "未分组")

    @property
    def logo(self) -> str:
        return self.attrs.get("tvg-logo", "")

    @property
    def response_time_ms(self) -> Optional[int]:
        value = self.attrs.get("response-time")
        if not value:
            return None
        match = re.search(r"(\d+)", value)
        if not match:
            return None
        return int(match.group(1))


@dataclass
class GroupedChannel:
    name: str
    group_title: str
    logo: str
    sources: List[ChannelEntry]
    slug: str


def parse_m3u(text: str) -> List[ChannelEntry]:
    entries: List[ChannelEntry] = []
    pending_extinf: Optional[Tuple[str, Dict[str, str], str]] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs, display_name = parse_extinf(line)
            pending_extinf = (line, attrs, display_name)
            continue
        if line.startswith("#"):
            continue
        if pending_extinf is None:
            continue

        raw_extinf, attrs, display_name = pending_extinf
        entries.append(
            ChannelEntry(
                display_name=display_name,
                url=line,
                attrs=attrs,
                raw_extinf=raw_extinf,
                order=len(entries),
            )
        )
        pending_extinf = None

    return entries


def parse_extinf(line: str) -> Tuple[Dict[str, str], str]:
    display_name = ""
    comma_index = _find_unquoted_comma(line)
    metadata = line
    if comma_index >= 0:
        metadata = line[:comma_index]
        display_name = line[comma_index + 1 :].strip()

    attrs = {
        match.group(1): match.group(2)
        for match in re.finditer(r'([\w-]+)="([^"]*)"', metadata)
    }
    if not display_name:
        display_name = attrs.get("tvg-name") or attrs.get("tvg-id") or "未命名频道"

    return attrs, display_name


def _find_unquoted_comma(text: str) -> int:
    in_quotes = False
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return index
    return -1


def normalize_channel_name(name: str) -> str:
    value = name.strip()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"（[^）]*）", "", value)
    value = value.replace("＋", "+").replace("﹢", "+")
    upper = value.upper()

    cctv = re.search(r"CCTV\s*-?\s*(\d{1,2})\s*(\+|PLUS|P)?", upper)
    if cctv:
        suffix = "+"
        plus_token = cctv.group(2)
        has_plus = plus_token in {"+", "PLUS", "P"} if plus_token else False
        return f"CCTV{int(cctv.group(1))}{suffix if has_plus else ''}"

    cgtn = re.search(r"\bCGTN\b\s*([A-Z ]+)?", upper)
    if cgtn:
        trailing = (cgtn.group(1) or "").strip()
        return " ".join(part for part in ["CGTN", trailing] if part)

    cleaned = value
    for word in QUALITY_WORDS:
        cleaned = re.sub(re.escape(word), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s_\-·|/]+", "", cleaned)
    cleaned = cleaned.strip()
    return cleaned or value


def group_channels(entries: Iterable[ChannelEntry]) -> Dict[str, GroupedChannel]:
    buckets: Dict[str, List[ChannelEntry]] = {}
    for entry in entries:
        name = _best_channel_name(entry)
        buckets.setdefault(name, []).append(entry)

    groups: Dict[str, GroupedChannel] = {}
    used_slugs: Dict[str, int] = {}
    for name, sources in buckets.items():
        sorted_sources = sorted(sources, key=_source_sort_key)
        slug = unique_slug(slugify_channel(name), used_slugs)
        first = sorted_sources[0]
        groups[name] = GroupedChannel(
            name=name,
            group_title=first.group_title,
            logo=first.logo,
            sources=sorted_sources,
            slug=slug,
        )

    return dict(sorted(groups.items(), key=lambda item: natural_sort_key(item[0])))


def _best_channel_name(entry: ChannelEntry) -> str:
    display = normalize_channel_name(entry.display_name)
    tvg_name = normalize_channel_name(entry.attrs.get("tvg-name", ""))
    tvg_id = normalize_channel_name(entry.attrs.get("tvg-id", ""))

    if display.endswith("+") and not tvg_name.endswith("+"):
        return display
    if tvg_name:
        return tvg_name
    if tvg_id:
        return tvg_id
    return display


def _source_sort_key(entry: ChannelEntry) -> Tuple[int, int, str]:
    response = entry.response_time_ms
    return (
        response if response is not None else 10_000_000,
        source_quality_rank(entry),
        entry.order,
        entry.url,
    )


def source_quality_rank(entry: ChannelEntry) -> int:
    haystack = " ".join(
        [
            entry.display_name,
            entry.raw_extinf,
            entry.url,
            " ".join(entry.attrs.values()),
        ]
    ).upper()
    if re.search(r"(?<!\d)720\s*P?(?!\d)", haystack):
        return 0
    if re.search(r"(?<!\d)1080\s*P?(?!\d)", haystack) or "FHD" in haystack:
        return 1
    return 2


def natural_sort_key(value: str) -> Tuple[str, int, str]:
    match = re.match(r"([A-Z]+)(\d+)(\+?)$", value.upper())
    if match:
        return (match.group(1), int(match.group(2)), match.group(3))
    return (value, 0, "")


def slugify_channel(name: str) -> str:
    value = name.strip().replace("+", "-plus")
    for chinese, replacement in sorted(CHINESE_SLUG_WORDS.items(), key=lambda item: -len(item[0])):
        value = value.replace(chinese, f"-{replacement}-")
    value = re.sub(r"[^A-Za-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-").lower()
    if value:
        return value
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"channel-{digest}"


def unique_slug(slug: str, used_slugs: Dict[str, int]) -> str:
    count = used_slugs.get(slug, 0)
    used_slugs[slug] = count + 1
    if count == 0:
        return slug
    return f"{slug}-{count + 1}"


def build_proxy_playlist(groups: Dict[str, GroupedChannel], base_url: str) -> str:
    base = base_url.rstrip("/")
    lines = [
        "#EXTM3U",
        f"#PLAYLIST: IPTV Failover Proxy",
        f"#DATE: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    for channel in groups.values():
        attrs = [
            f'tvg-name="{escape_attr(channel.name)}"',
            f'group-title="{escape_attr(channel.group_title)}"',
            f'sources="{len(channel.sources)}"',
        ]
        if channel.logo:
            attrs.insert(1, f'tvg-logo="{escape_attr(channel.logo)}"')
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{channel.name}")
        lines.append(f"{base}/live/{channel.slug}.m3u8")
    return "\n".join(lines) + "\n"


def build_tvbox_txt(groups: Dict[str, GroupedChannel]) -> str:
    lines: List[str] = []
    current_group = None
    channels = sorted(groups.values(), key=_tvbox_channel_sort_key)

    for channel in channels:
        group_title = tvbox_group_title(channel)
        if group_title != current_group:
            if lines:
                lines.append("")
            current_group = group_title
            lines.append(f"{current_group},#genre#")
        for source in channel.sources:
            lines.append(f"{channel.name},{source.url}")

    return "\n".join(lines) + "\n"


def _tvbox_channel_sort_key(channel: GroupedChannel) -> Tuple[int, Tuple[str, int, str]]:
    return (TVBOX_GROUP_ORDER.index(tvbox_group_title(channel)), natural_sort_key(channel.name))


def tvbox_group_title(channel: GroupedChannel) -> str:
    upper_name = channel.name.upper()
    if upper_name.startswith("CCTV") or upper_name.startswith("CGTN"):
        return "央视"
    if channel.name.endswith("卫视"):
        return "卫视"
    return "其他"


def escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def index_by_slug(groups: Dict[str, GroupedChannel]) -> Dict[str, GroupedChannel]:
    return {channel.slug: channel for channel in groups.values()}
