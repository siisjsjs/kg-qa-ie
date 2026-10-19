"""workers：异步任务入口（BackgroundTasks → Celery 渐进式）。

骨架阶段：使用 FastAPI BackgroundTasks 同进程执行；后续切换 Celery 不影响调用层接口。
"""
