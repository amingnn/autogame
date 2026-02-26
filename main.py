import os
import time

import uvicorn
from threading import Thread
from skyland_sign.skyland import start as sky_start

from tools import mlog, time_wrapper, status, push_wechat


def run_webhook():
    """在线程中启动 Webhook"""
    # 注意：在线程中运行必须设置 reload=False
    uvicorn.run("tools.webhook:app", host="127.0.0.1", port=8000, reload=False)
def run_sky_land():
    """运行签到任务并更新状态"""
    successs, _ = sky_start()
    if successs:
        status['sky_land'] = True
    else:
        mlog.error(time_wrapper("森空岛签到失败"))


if __name__ == '__main__':
    os.startfile(r"C:\Users\GamerBot\Desktop\MaaEnd.exe - 快捷方式.lnk")
    webhook_thread = Thread(target=run_webhook, daemon=True)
    webhook_thread.start()
    sky_thread = Thread(target=run_sky_land)
    sky_thread.start()
    print("--- 系统监控中：等待所有自动化任务完成 ---")

    # 3. 主线程循环监控
    start_time = time.time()
    TIMEOUT = 3600  # 1小时保底超时

    while True:
        # 【关键】这里需要你的 Webhook 端在收到 'maa' 和 'end' 消息时
        # 修改这个全局变量 status['maa'] = True

        # 统计已完成数量
        done_tasks = [k for k, v in status.items() if v]
        print(f"当前进度: {len(done_tasks)}/3 {done_tasks}")

        if all(status.values()):
            mlog.info(time_wrapper("\n **所有任务均已成功完成！**"))
            break

        if time.time() - start_time > TIMEOUT:
            mlog.error(time_wrapper(f"\n[!] 监控超时，为安全起见准备关机. e: {done_tasks}"))
            break

        time.sleep(5)

    # 推送微信
    push_wechat()

    # 执行关机
    print("系统将在 60 秒后关机...")
    os.system("shutdown /s /t 60")
