import json
import time

print('示例任务开始：验证插件、独立进程和实时日志', flush=True)
time.sleep(1)
print('AUTOMATION_RESULT=' + json.dumps({'status':'success','message':'示例任务完成'}, ensure_ascii=False), flush=True)
