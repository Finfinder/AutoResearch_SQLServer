# stats_parser.py
import re
import xml.etree.ElementTree as ET

_SHOWPLAN_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_IO_PATTERN = re.compile(
    r"Scan count \d+, "
    r"logical reads (\d+), "
    r"physical reads (\d+), "
    r".*?read-ahead reads (\d+), "
    r".*?lob logical reads (\d+), "
    r"lob physical reads (\d+)",
    re.DOTALL | re.IGNORECASE,
)

_TIME_PATTERN = re.compile(
    r"SQL Server Execution Times:.*?CPU time = (\d+) ms,\s+elapsed time = (\d+) ms",
    re.DOTALL | re.IGNORECASE,
)


def parse_io_stats(messages):
    totals = {
        "logical_reads": 0,
        "physical_reads": 0,
        "read_ahead_reads": 0,
        "lob_logical_reads": 0,
        "lob_physical_reads": 0,
    }
    found = False
    for _, msg in messages:
        for match in _IO_PATTERN.finditer(msg):
            found = True
            totals["logical_reads"] += int(match.group(1))
            totals["physical_reads"] += int(match.group(2))
            totals["read_ahead_reads"] += int(match.group(3))
            totals["lob_logical_reads"] += int(match.group(4))
            totals["lob_physical_reads"] += int(match.group(5))
    return totals if found else {}


def parse_time_stats(messages):
    for _, msg in messages:
        match = _TIME_PATTERN.search(msg)
        if match:
            return {
                "cpu_time_ms": int(match.group(1)),
                "elapsed_time_ms": int(match.group(2)),
            }
    return {}


def _extract_runtime_stats(root, ns):
    runtime = {}
    qts = root.find(".//sp:QueryTimeStats", ns)
    if qts is not None:
        cpu = qts.get("CpuTime")
        elapsed = qts.get("ElapsedTime")
        if cpu is not None:
            runtime["cpu_time_ms"] = int(cpu)
        if elapsed is not None:
            runtime["elapsed_time_ms"] = int(elapsed)
    total_logical = 0
    total_physical = 0
    total_readahead = 0
    found_io = False
    for counter in root.findall(".//sp:RunTimeCountersPerThread", ns):
        lr = counter.get("ActualLogicalReads")
        if lr is not None:
            found_io = True
            total_logical += int(lr)
            total_physical += int(counter.get("ActualPhysicalReads", 0))
            total_readahead += int(counter.get("ActualReadAheads", 0))
    if found_io:
        runtime["logical_reads"] = total_logical
        runtime["physical_reads"] = total_physical
        runtime["read_ahead_reads"] = total_readahead
    return runtime


def parse_execution_plan(xml_string):
    if not xml_string:
        return {}
    try:
        clean_xml = xml_string
        if clean_xml.lstrip().startswith("<?xml"):
            end_idx = clean_xml.find("?>")
            if end_idx != -1:
                clean_xml = clean_xml[end_idx + 2:].lstrip()
        root = ET.fromstring(clean_xml)
        ns = {"sp": _SHOWPLAN_NS}
        result = {
            "memory_grant_kb": None,
            "spill_warnings": [],
            "physical_operators": [],
            "runtime_stats": {},
        }
        query_plan = root.find(".//sp:QueryPlan", ns)
        if query_plan is not None:
            mg = query_plan.get("MemoryGrant")
            if mg is not None:
                result["memory_grant_kb"] = int(mg)
        for spill in root.findall(".//sp:SpillToTempDb", ns):
            result["spill_warnings"].append(spill.attrib.copy())
        operators = set()
        for relop in root.findall(".//sp:RelOp", ns):
            op = relop.get("PhysicalOp")
            if op:
                operators.add(op)
        result["physical_operators"] = sorted(operators)

        runtime = _extract_runtime_stats(root, ns)
        if runtime:
            result["runtime_stats"] = runtime

        return result
    except ET.ParseError:
        return {}
