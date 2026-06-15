# TVBox IPTV Subscription

自动从公开源生成影视仓 / TVBox 更容易识别的 `txt` 直播订阅。

## Subscription

```text
https://raw.githubusercontent.com/doubletree6/tvbox-iptv-subscription/main/cn_all_tvbox_multi.txt
```

## Rules

- Source: `https://raw.githubusercontent.com/best-fan/iptv-sources/master/cn_all.m3u8`
- Output: `cn_all_tvbox_multi.txt`
- Format: TVBox `txt` live format, repeated channel names for multiple lines
- Groups: only `央视`, `卫视`, `其他`
- Source order inside each channel: `720p` first, then `1080p`, then other/unknown quality
- Update schedule: twice daily at 08:15 and 20:15 Beijing time

## Manual Build

```bash
python3 -m unittest discover -s tests
python3 -m iptv_failover.server --once cn_all_tvbox_multi.txt --format tvbox-txt
```

