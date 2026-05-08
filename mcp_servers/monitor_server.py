"""智能运维监控 MCP Server

本地实现的监控服务 MCP Server，提供：
- 监控数据查询（CPU、内存、磁盘、网络等）
- 进程信息查询
- 历史工单查询
- 服务信息查询

用于支持运维 Agent 的故障排查场景。
"""

import logging
import functools
import json
import os
import random
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from fastmcp import FastMCP

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Monitor_MCP_Server")

mcp = FastMCP("Monitor")
ROOT_DIR = Path(__file__).resolve().parents[1]
DISK_MOCK_PATH = ROOT_DIR / "mock_data" / "disk.json"


def log_tool_call(func):
    """装饰器：记录工具调用的日志，包括方法名、参数和返回状态"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__

        # 记录调用信息
        logger.info(f"=" * 80)
        logger.info(f"调用方法: {method_name}")

        # 记录参数（排除self等）
        if kwargs:
            # 使用 json.dumps 格式化参数，处理可能的序列化错误
            try:
                params_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                params_str = str(kwargs)
            logger.info(f"参数信息:\n{params_str}")
        else:
            logger.info("参数信息: 无")

        # 执行方法
        try:
            result = func(*args, **kwargs)

            # 记录返回状态
            logger.info(f"返回状态: SUCCESS")

            # 记录返回结果摘要（避免日志过长）
            if isinstance(result, dict):
                summary = {k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                          for k, v in list(result.items())[:5]}
                logger.info(f"返回结果摘要: {json.dumps(summary, ensure_ascii=False)}")
            else:
                logger.info(f"返回结果: {result}")

            logger.info(f"=" * 80)
            return result

        except Exception as e:
            # 记录错误状态
            logger.error(f"返回状态: ERROR")
            logger.error(f"错误信息: {str(e)}")
            logger.error(f"=" * 80)
            raise

    return wrapper


# ============================================================
# 辅助函数
# ============================================================

def parse_time_or_default(time_str: Optional[str], default_offset_hours: int = 0) -> datetime:
    """解析时间字符串或返回默认时间。

    Args:
        time_str: 时间字符串（格式：YYYY-MM-DD HH:MM:SS）
        default_offset_hours: 默认时间偏移（小时）

    Returns:
        datetime: 解析后的时间对象
    """
    if time_str:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    # 返回默认时间（当前时间 + 偏移）
    return datetime.now() + timedelta(hours=default_offset_hours)


def generate_time_series(base_time: datetime, minutes_offset: int, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """生成时间序列字符串。

    Args:
        base_time: 基准时间
        minutes_offset: 分钟偏移量
        format_str: 时间格式字符串

    Returns:
        str: 格式化的时间字符串
    """
    result_time = base_time + timedelta(minutes=minutes_offset)
    return result_time.strftime(format_str)


def _load_mock_active_alerts(include_resolved: bool = False) -> list[dict[str, Any]]:
    """Load mock active alerts.

    Environment override:
    - MOCK_ACTIVE_ALERTS=empty|none|0  => return no active alerts
    - MOCK_ACTIVE_ALERTS=<json-array>  => return parsed alerts
    """
    raw = os.getenv("MOCK_ACTIVE_ALERTS", "").strip()
    if raw.lower() in {"empty", "none", "0"}:
        return []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            logger.warning("MOCK_ACTIVE_ALERTS 不是有效 JSON，回退到默认 mock 告警")

    alerts = [
        {
            "alert_name": "HighCPUUsage",
            "severity": "critical",
            "service_name": "data-sync-service",
            "instance": "data-sync-service-01",
            "duration": "12m",
            "description": "CPU 使用率持续超过 80%",
            "status": "firing",
            "source": "mock-monitor",
        }
    ]
    if include_resolved:
        alerts.append(
            {
                "alert_name": "MemoryPressureRecovered",
                "severity": "warning",
                "service_name": "data-sync-service",
                "instance": "data-sync-service-01",
                "duration": "28m",
                "description": "内存压力已恢复到正常范围",
                "status": "resolved",
                "source": "mock-monitor",
            }
        )
    return alerts


def _load_disk_mock_data() -> dict[str, Any]:
    """Load mock disk diagnostic data from disk.json."""
    with DISK_MOCK_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)





# ============================================================
# 监控数据查询工具
# ============================================================


@mcp.tool()
@log_tool_call
def get_active_alerts(include_resolved: bool = False) -> Dict[str, Any]:
    """获取当前系统的活跃告警列表。"""
    alerts = _load_mock_active_alerts(include_resolved=include_resolved)
    active_alerts = [alert for alert in alerts if alert.get("status") != "resolved"]
    return {
        "active_alerts": active_alerts,
        "total": len(active_alerts),
        "include_resolved": include_resolved,
        "message": "已返回 mock 活跃告警列表" if active_alerts else "当前未检测到活跃告警",
    }


@mcp.tool()
@log_tool_call
def list_active_alerts(include_resolved: bool = False) -> Dict[str, Any]:
    """获取当前系统的活跃告警列表（get_active_alerts 的别名）。"""
    return get_active_alerts(include_resolved=include_resolved)

@mcp.tool()
@log_tool_call
def query_cpu_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """查询服务的 CPU 使用率监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"
            说明: 控制数据点的时间间隔

    Returns:
        Dict: CPU 监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (cpu_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: CPU 使用率百分比
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_cpu_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
        
        # 示例3: 只指定开始时间（结束时间自动为当前时间）
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00"
        )
    """
    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    
    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1  # 默认 1 分钟
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60

    # 动态生成 CPU 使用率数据：从低到高逐渐增长
    data_points = []
    current_time = start_dt
    time_index = 0

    # 初始 CPU 使用率（10%）
    base_cpu = 10.0

    while current_time <= end_dt:
        # CPU 使用率逐渐升高的算法：
        # - 前几个数据点保持在 10% 左右
        # - 然后开始快速上升
        # - 最终达到 95% 左右

        if time_index < 3:
            # 初始阶段：10% 左右波动
            cpu_value = base_cpu + (time_index * 0.5)
        else:
            # 上升阶段：使用指数增长模型
            growth_factor = (time_index - 2) * 8.5
            cpu_value = min(base_cpu + growth_factor, 96.0)

        # 添加一些随机波动（±2%）
        cpu_value = round(cpu_value + random.uniform(-2, 2), 1)
        cpu_value = max(0, min(100, cpu_value))  # 确保在 0-100 范围内

        data_point = {
            "timestamp": current_time.strftime("%H:%M"),
            "value": cpu_value,
            "process_id": "pid-12345"
        }

        data_points.append(data_point)

        # 下一个时间点
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1

    # 计算统计信息
    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)

        # 检测是否有 CPU 突增（超过 80%）
        spike_detected = max_value > 80.0

        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "spike_detected": spike_detected
            },
            "alert_info": {
                "triggered": spike_detected,
                "threshold": 80.0,
                "message": "CPU 使用率持续超过 80% 阈值" if spike_detected else "CPU 使用率正常"
            }
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
        }


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """查询服务的内存使用监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"

    Returns:
        Dict: 内存监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (memory_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: 内存使用率百分比
                * used_gb: 已使用内存（GB）
                * total_gb: 总内存（GB）
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_memory_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_memory_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
    """
    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    
    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1  # 默认 1 分钟
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60
    
    # 动态生成内存使用率数据：从低到高逐渐增长
    data_points = []
    current_time = start_dt
    time_index = 0
    
    # 初始内存使用率（30%）
    base_memory = 30.0
    total_gb = 8.0  # 总内存 8GB
    
    while current_time <= end_dt:
        # 内存使用率逐渐升高的算法：
        # - 前几个数据点保持在 30% 左右
        # - 然后开始逐步上升
        # - 最终达到 85% 左右
        
        if time_index < 3:
            # 初始阶段：30% 左右波动
            memory_value = base_memory + (time_index * 1.0)
        else:
            # 上升阶段：使用线性增长模型（内存增长比 CPU 慢）
            growth_factor = (time_index - 2) * 5.5
            memory_value = min(base_memory + growth_factor, 85.0)
        
        # 添加一些随机波动（±1%）
        memory_value = round(memory_value + random.uniform(-1, 1), 1)
        memory_value = max(0, min(100, memory_value))  # 确保在 0-100 范围内
        
        # 计算已使用内存（GB）
        used_gb = round((memory_value / 100.0) * total_gb, 2)
        
        data_point = {
            "timestamp": current_time.strftime("%H:%M"),
            "value": memory_value,
            "used_gb": used_gb,
            "total_gb": total_gb
        }
        
        data_points.append(data_point)
        
        # 下一个时间点
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1
    
    # 计算统计信息
    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)
        
        # 检测是否有内存压力（超过 70%）
        memory_pressure = max_value > 70.0
        
        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "memory_pressure": memory_pressure
            },
            "alert_info": {
                "triggered": memory_pressure,
                "threshold": 70.0,
                "message": "内存使用率超过 70% 阈值，存在内存压力" if memory_pressure else "内存使用率正常"
            }
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
            "error": "时间范围无效或没有生成数据点"
        }


@mcp.tool()
@log_tool_call
def query_process_list(service_name: str) -> Dict[str, Any]:
    """查询指定服务的进程列表和资源热点。"""
    return {
        "service_name": service_name,
        "instance_count": 2,
        "processes": [
            {
                "instance": f"{service_name}-01",
                "pid": 12345,
                "cpu_percent": 93.6,
                "memory_percent": 71.3,
                "command": "python worker.py --sync-loop",
            },
            {
                "instance": f"{service_name}-02",
                "pid": 12378,
                "cpu_percent": 27.4,
                "memory_percent": 42.1,
                "command": "python api.py",
            },
        ],
        "message": f"已返回 {service_name} 的 mock 进程列表",
    }


@mcp.tool()
@log_tool_call
def search_historical_tickets(service_name: str, alert_name: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    """查询服务相关的历史工单。"""
    alert_label = alert_name or "HighCPUUsage"
    tickets = [
        {
            "ticket_id": "INC-2026-0214",
            "service_name": service_name,
            "alert_name": alert_label,
            "status": "resolved",
            "summary": "批处理任务重试风暴导致 CPU 持续升高",
            "root_cause": "下游接口超时后重试退避失效，worker 数量堆积",
            "resolution": "降低并发并修复重试退避参数",
        },
        {
            "ticket_id": "INC-2026-0130",
            "service_name": service_name,
            "alert_name": alert_label,
            "status": "resolved",
            "summary": "同步任务积压触发 CPU 告警",
            "root_cause": "Kafka backlog 激增，单实例消费过载",
            "resolution": "扩容消费者并限制单批次处理量",
        },
    ]
    return {
        "service_name": service_name,
        "alert_name": alert_label,
        "total": min(limit, len(tickets)),
        "tickets": tickets[:limit],
        "message": f"已返回 {service_name} 的 mock 历史工单",
    }


@mcp.tool()
@log_tool_call
def get_service_info(service_name: str) -> Dict[str, Any]:
    """查询服务基础信息。"""
    return {
        "service_name": service_name,
        "owner_team": "data-platform",
        "runtime": "python3.11",
        "deployment": "kubernetes",
        "instances": [
            {"instance": f"{service_name}-01", "zone": "ap-beijing-a", "status": "running"},
            {"instance": f"{service_name}-02", "zone": "ap-beijing-b", "status": "running"},
        ],
        "dependencies": ["kafka-sync-topic", "redis-lock", "order-db"],
        "message": f"已返回 {service_name} 的 mock 服务信息",
    }


@mcp.tool()
@log_tool_call
def list_all_services() -> Dict[str, Any]:
    """列出当前系统中的 mock 服务。"""
    services = [
        {"service_name": "data-sync-service", "status": "running", "owner_team": "data-platform"},
        {"service_name": "api-gateway-service", "status": "running", "owner_team": "gateway-team"},
        {"service_name": "billing-worker", "status": "running", "owner_team": "finance-platform"},
    ]
    return {
        "total": len(services),
        "services": services,
        "message": "已返回 mock 服务列表",
    }




@mcp.tool()
@log_tool_call
def get_disk_usage(hostname: Optional[str] = None, mount: str = "/") -> Dict[str, Any]:
    """Return mock disk usage for a host and mount point."""
    payload = _load_disk_mock_data()
    disk_usage = dict(payload.get("disk_usage", {}))
    if hostname:
        disk_usage["host"] = hostname
    disk_usage["mount"] = mount or disk_usage.get("mount", "/")
    usage_percent = float(disk_usage.get("usage_percent", 0))
    disk_usage["status"] = "critical" if usage_percent >= 90 else "warning" if usage_percent >= 80 else "healthy"
    return disk_usage


@mcp.tool()
@log_tool_call
def list_large_directories(path: str = "/", limit: int = 10) -> Dict[str, Any]:
    """Return the top large directories from mock data."""
    payload = _load_disk_mock_data()
    reason_map = {
        "/var/log": "业务日志与归档日志堆积",
        "/var/lib/docker": "Docker 镜像、卷或构建缓存占用",
        "/tmp": "临时文件未定期清理",
        "/app/cache": "应用缓存未过期或未淘汰",
    }
    directories = []
    for item in list(payload.get("large_directories", []) or [])[:limit]:
        directory = dict(item)
        directory["reason"] = directory.get("reason") or reason_map.get(
            str(directory.get("path", "")),
            "目录占用偏高，需要进一步核查内容组成",
        )
        directories.append(directory)
    return {
        "path": path,
        "limit": limit,
        "directories": directories,
    }


@mcp.tool()
@log_tool_call
def list_large_files(path: str = "/", min_size_mb: int = 100, limit: int = 20) -> Dict[str, Any]:
    """Return mock large files above a size threshold."""
    payload = _load_disk_mock_data()
    files = list(payload.get("large_files", []) or [])
    min_size_gb = round(min_size_mb / 1024, 3)
    filtered = []
    for item in files:
        if float(item.get("size_gb", 0)) < min_size_gb:
            continue
        file_item = dict(item)
        file_path = str(file_item.get("path", ""))
        if "safe_action" not in file_item:
            file_item["safe_action"] = (
                "先确认日志保留策略，再执行轮转、压缩或归档"
                if file_path.lower().endswith(".log")
                else "需要结合业务影响评估后再处理"
            )
        if "risk" not in file_item:
            file_item["risk"] = (
                "直接删除可能影响审计、排障或业务写入"
                if file_path.lower().endswith(".log")
                else "需要确认文件是否被在线业务依赖"
            )
        filtered.append(file_item)
    return {
        "path": path,
        "min_size_mb": min_size_mb,
        "limit": limit,
        "files": filtered[:limit],
    }


@mcp.tool()
@log_tool_call
def query_deleted_open_files() -> Dict[str, Any]:
    """Return deleted-but-still-open files from mock data."""
    payload = _load_disk_mock_data()
    files = []
    for item in list(payload.get("deleted_open_files", []) or []):
        file_item = dict(item)
        process_name = file_item.get("process") or file_item.get("process_name") or ""
        file_item["process"] = process_name
        file_item["file"] = file_item.get("file") or file_item.get("path")
        file_item["suggestion"] = file_item.get("suggestion") or (
            f"在业务低峰平滑重启 {process_name}，释放已删除但未归还的磁盘空间"
            if process_name
            else "确认句柄所属进程后再安排平滑重启释放空间"
        )
        files.append(file_item)
    return {
        "files": files,
        "total": len(files),
    }


@mcp.tool()
@log_tool_call
def query_docker_disk_usage() -> Dict[str, Any]:
    """Return Docker disk usage from mock data."""
    payload = _load_disk_mock_data()
    return dict(payload.get("docker_usage", {}))


@mcp.tool()
@log_tool_call
def get_disk_cleanup_candidates() -> Dict[str, Any]:
    """Return structured cleanup candidates from mock data."""
    payload = _load_disk_mock_data()
    data = dict(payload.get("cleanup_candidates", {}))

    def normalize(items: Any, needs_reason: bool = False) -> list[dict[str, Any]]:
        normalized_items = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if needs_reason:
                entry["reason"] = entry.get("reason") or "高风险或禁止自动执行"
            else:
                entry["suggestion"] = entry.get("suggestion") or "需要人工确认后再执行"
            normalized_items.append(entry)
        return normalized_items

    return {
        "safe": normalize(data.get("safe")),
        "need_approval": normalize(data.get("need_approval")),
        "forbidden": normalize(data.get("forbidden"), needs_reason=True),
    }


if __name__ == "__main__":
    # 使用 streamable-http 模式，运行在 8004 端口
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
