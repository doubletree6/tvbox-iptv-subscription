import unittest

from iptv_failover.core import (
    ChannelEntry,
    build_proxy_playlist,
    build_tvbox_txt,
    group_channels,
    normalize_channel_name,
    parse_m3u,
    slugify_channel,
)


SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-name="CCTV1" tvg-logo="logo1.png" group-title="央视频道" response-time="31ms",CCTV1
http://a.example/live/cctv1.m3u8
#EXTINF:-1 tvg-id="CCTV1.cn@HD",CCTV-1 (1080p)
http://b.example/live/cctv1.m3u8
#EXTINF:-1 tvg-name="CCTV5" group-title="央视频道" response-time="120ms",CCTV-5+
http://a.example/live/cctv5p.m3u8
#EXTINF:-1 tvg-name="CCTV5+" group-title="体育频道" response-time="29ms",CCTV5+
http://b.example/live/cctv5p.m3u8
"""

QUALITY_SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-name="CCTV1" group-title="央视频道" response-time="10ms",CCTV-1 (1080p)
http://a.example/live/cctv1-1080p.m3u8
#EXTINF:-1 tvg-name="CCTV1" group-title="央视频道" response-time="80ms",CCTV-1 (720p)
http://b.example/live/cctv1-720p.m3u8
#EXTINF:-1 tvg-name="CCTV1" group-title="央视频道" response-time="1ms",CCTV1
http://c.example/live/cctv1.m3u8
#EXTINF:-1 tvg-name="北京卫视" group-title="卫视频道" response-time="1ms",北京卫视
http://d.example/live/beijing.m3u8
#EXTINF:-1 tvg-name="凤凰中文" group-title="其他频道" response-time="1ms",凤凰中文
http://e.example/live/ifeng.m3u8
"""


class IptvFailoverTests(unittest.TestCase):
    def test_parse_m3u_extracts_extinf_metadata(self):
        entries = parse_m3u(SAMPLE)

        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].display_name, "CCTV1")
        self.assertEqual(entries[0].attrs["tvg-name"], "CCTV1")
        self.assertEqual(entries[0].group_title, "央视频道")
        self.assertEqual(entries[0].response_time_ms, 31)
        self.assertEqual(entries[0].url, "http://a.example/live/cctv1.m3u8")

    def test_normalize_channel_name_handles_cctv_variants(self):
        self.assertEqual(normalize_channel_name("CCTV-1 (1080p)"), "CCTV1")
        self.assertEqual(normalize_channel_name("CCTV-4 中文国际"), "CCTV4")
        self.assertEqual(normalize_channel_name("CCTV-5+"), "CCTV5+")
        self.assertEqual(normalize_channel_name("CCTV5+"), "CCTV5+")

    def test_group_channels_prefers_display_name_for_plus_channels(self):
        entries = parse_m3u(SAMPLE)
        groups = group_channels(entries)

        self.assertEqual(set(groups), {"CCTV1", "CCTV5+"})
        self.assertEqual(len(groups["CCTV1"].sources), 2)
        self.assertEqual(len(groups["CCTV5+"].sources), 2)
        self.assertEqual(groups["CCTV5+"].group_title, "体育频道")

    def test_build_proxy_playlist_outputs_one_entry_per_channel(self):
        groups = group_channels(parse_m3u(SAMPLE))
        playlist = build_proxy_playlist(groups, base_url="http://127.0.0.1:8899")

        self.assertEqual(playlist.count("#EXTINF"), 2)
        self.assertIn('tvg-name="CCTV1"', playlist)
        self.assertIn("http://127.0.0.1:8899/live/cctv1.m3u8", playlist)
        self.assertIn("http://127.0.0.1:8899/live/cctv5-plus.m3u8", playlist)

    def test_slugify_channel_is_stable(self):
        self.assertEqual(slugify_channel("CCTV5+"), "cctv5-plus")
        self.assertEqual(slugify_channel("北京卫视"), "bei-jing-wei-shi")

    def test_sources_are_sorted_by_response_time_when_present(self):
        channel = group_channels(parse_m3u(SAMPLE))["CCTV5+"]

        self.assertEqual(channel.sources[0].url, "http://b.example/live/cctv5p.m3u8")
        self.assertEqual(channel.sources[1].url, "http://a.example/live/cctv5p.m3u8")

    def test_build_tvbox_txt_repeats_same_channel_name_for_lines(self):
        groups = group_channels(parse_m3u(SAMPLE))
        playlist = build_tvbox_txt(groups)

        self.assertIn("央视,#genre#", playlist)
        self.assertIn("CCTV1,http://a.example/live/cctv1.m3u8", playlist)
        self.assertIn("CCTV1,http://b.example/live/cctv1.m3u8", playlist)
        self.assertNotIn("CCTV1 线路", playlist)

    def test_build_tvbox_txt_uses_only_three_groups(self):
        groups = group_channels(parse_m3u(QUALITY_SAMPLE))
        playlist = build_tvbox_txt(groups)

        self.assertIn("央视,#genre#", playlist)
        self.assertIn("卫视,#genre#", playlist)
        self.assertIn("其他,#genre#", playlist)
        self.assertNotIn("央视频道,#genre#", playlist)
        self.assertNotIn("卫视频道,#genre#", playlist)

    def test_sources_put_720p_before_1080p_and_other_quality(self):
        channel = group_channels(parse_m3u(QUALITY_SAMPLE))["CCTV1"]

        self.assertEqual(channel.sources[0].url, "http://b.example/live/cctv1-720p.m3u8")
        self.assertEqual(channel.sources[1].url, "http://a.example/live/cctv1-1080p.m3u8")
        self.assertEqual(channel.sources[2].url, "http://c.example/live/cctv1.m3u8")


if __name__ == "__main__":
    unittest.main()
