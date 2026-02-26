import logging
import requests
from pathlib import Path
from datetime import datetime


# 定义日志基础路径
log_dir = Path("~/Desktop/log").expanduser()
log_dir.mkdir(parents=True, exist_ok=True)
# 生成文件名
log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d_%H')}.log"


mlog = logging.getLogger("MAA")
mlog.propagate = False # 关键：防止日志向上传递给 Root Logger 导致重复打印
# 给你的专属 Logger 绑定处理器
if not mlog.handlers:
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    mlog.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter('%(message)s'))
    mlog.addHandler(fh)


def mlog_info(message, with_time=False):
    if with_time:
        mlog.info(time_wrapper(message))
    else:
        # 纯消息
        mlog.info(message)

def time_wrapper(message):
    timestamp = datetime.now().strftime("[%H:%M:%S] ")
    return f"{timestamp}{message}"


def log_wrapper(content: str, title: str = None):
    """
    包装函数：传入标题和内容，自动进行排版并写入日志
    """
    frt = "-" * 10
    title = title if title else frt
    formatted_msg = (
        f"```text\n"
        f"{frt}{title}{frt}\n"
        f"{content.strip()}\n"
        f"{frt}{frt}{frt}\n"
        f"```\n"
    )
    return formatted_msg  # 返回出去方便 push_wechat 使用


def push_wechat():
    with open(log_file, mode="r", encoding='utf-8') as f:
        content = f.read()
    send_key = "SCT210449Tz0xPFVEiPkRRc3FQxp2vIp85"
    url = f'https://sctapi.ftqq.com/{send_key}.send'
    headers = {'Content-Type': 'application/json;charset=utf-8'}
    # 构建 Server 酱请求
    params = {
        "title": "自动化任务报告",
        "desp": content
    }
    requests.post(url, json=params, headers=headers)

if __name__ == '__main__':
    push_wechat()
