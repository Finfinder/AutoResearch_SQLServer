# tests/test_stats_parser.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stats_parser import parse_io_stats, parse_time_stats, parse_execution_plan

_IO_SINGLE = (
    "01000",
    "[01000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
    "Table 'SalesOrderHeader'. Scan count 1, logical reads 689, physical reads 0, "
    "page server reads 0, read-ahead reads 0, page server read-ahead reads 0, "
    "lob logical reads 0, lob physical reads 0, lob page server reads 0, "
    "lob read-ahead reads 0, lob page server read-ahead reads 0.",
)

_IO_TABLE2 = (
    "01000",
    "[01000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
    "Table 'SalesOrderDetail'. Scan count 1, logical reads 50, physical reads 2, "
    "page server reads 0, read-ahead reads 10, page server read-ahead reads 0, "
    "lob logical reads 5, lob physical reads 1, lob page server reads 0, "
    "lob read-ahead reads 0, lob page server read-ahead reads 0.",
)

_TIME_COMPILE = (
    "01000",
    "[01000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
    "SQL Server parse and compile time: \n   CPU time = 0 ms, elapsed time = 1 ms.",
)

_TIME_EXEC = (
    "01000",
    "[01000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
    "SQL Server Execution Times:\n   CPU time = 15 ms,  elapsed time = 267 ms.",
)

_PLAN_XML = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" '
    'Version="1.7" Build="15.0.4153.1">'
    "<BatchSequence><Batch><Statements>"
    '<StmtSimple StatementType="SELECT">'
    '<QueryPlan DegreeOfParallelism="1" MemoryGrant="1024">'
    '<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="100">'
    '<RelOp NodeId="1" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="100" />'
    '<RelOp NodeId="2" PhysicalOp="Sort" LogicalOp="Sort" EstimateRows="100" />'
    "</RelOp>"
    "</QueryPlan>"
    "</StmtSimple>"
    "</Statements></Batch></BatchSequence>"
    "</ShowPlanXML>"
)

_PLAN_XML_WITH_RUNTIME = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" '
    'Version="1.7" Build="15.0.4153.1">'
    "<BatchSequence><Batch><Statements>"
    '<StmtSimple StatementType="SELECT">'
    '<QueryPlan DegreeOfParallelism="1" MemoryGrant="1024">'
    '<QueryTimeStats CpuTime="15" ElapsedTime="267" />'
    '<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="100">'
    "<RunTimeInformation>"
    '<RunTimeCountersPerThread Thread="0" ActualRows="100" ActualExecutions="1" />'
    "</RunTimeInformation>"
    '<RelOp NodeId="1" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="100">'
    "<RunTimeInformation>"
    '<RunTimeCountersPerThread Thread="0" ActualRows="100" ActualLogicalReads="689" '
    'ActualPhysicalReads="5" ActualReadAheads="10" ActualExecutions="1" />'
    "</RunTimeInformation>"
    "</RelOp>"
    '<RelOp NodeId="2" PhysicalOp="Index Seek" LogicalOp="Index Seek" EstimateRows="100">'
    "<RunTimeInformation>"
    '<RunTimeCountersPerThread Thread="0" ActualRows="100" ActualLogicalReads="50" '
    'ActualPhysicalReads="2" ActualReadAheads="0" ActualExecutions="1" />'
    "</RunTimeInformation>"
    "</RelOp>"
    "</RelOp>"
    "</QueryPlan>"
    "</StmtSimple>"
    "</Statements></Batch></BatchSequence>"
    "</ShowPlanXML>"
)

_PLAN_XML_WITH_SPILL = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" '
    'Version="1.7" Build="15.0.4153.1">'
    "<BatchSequence><Batch><Statements>"
    '<StmtSimple StatementType="SELECT">'
    '<QueryPlan DegreeOfParallelism="1" MemoryGrant="2048">'
    "<Warnings>"
    '<SpillToTempDb SpillLevel="1" SpilledThreadCount="0" />'
    "</Warnings>"
    '<RelOp NodeId="0" PhysicalOp="Sort" LogicalOp="Sort" EstimateRows="100" />'
    "</QueryPlan>"
    "</StmtSimple>"
    "</Statements></Batch></BatchSequence>"
    "</ShowPlanXML>"
)


class TestParseIoStats:
    def test_single_table(self):
        result = parse_io_stats([_IO_SINGLE])
        assert result["logical_reads"] == 689
        assert result["physical_reads"] == 0
        assert result["read_ahead_reads"] == 0
        assert result["lob_logical_reads"] == 0
        assert result["lob_physical_reads"] == 0

    def test_multi_table_sums(self):
        result = parse_io_stats([_IO_SINGLE, _IO_TABLE2])
        assert result["logical_reads"] == 689 + 50
        assert result["physical_reads"] == 0 + 2
        assert result["read_ahead_reads"] == 0 + 10
        assert result["lob_logical_reads"] == 0 + 5
        assert result["lob_physical_reads"] == 0 + 1

    def test_empty_messages_returns_empty_dict(self):
        assert parse_io_stats([]) == {}

    def test_no_io_data_returns_empty_dict(self):
        assert parse_io_stats([("01000", "Some unrelated message")]) == {}


class TestParseTimeStats:
    def test_execution_times_extracted(self):
        result = parse_time_stats([_TIME_COMPILE, _TIME_EXEC])
        assert result["cpu_time_ms"] == 15
        assert result["elapsed_time_ms"] == 267

    def test_parse_compile_ignored_when_combined_in_same_message(self):
        combined = (
            "01000",
            "SQL Server parse and compile time: \n   CPU time = 0 ms, elapsed time = 1 ms.\n"
            "SQL Server Execution Times:\n   CPU time = 15 ms,  elapsed time = 267 ms.",
        )
        result = parse_time_stats([combined])
        assert result["cpu_time_ms"] == 15
        assert result["elapsed_time_ms"] == 267

    def test_empty_messages_returns_empty_dict(self):
        assert parse_time_stats([]) == {}

    def test_no_time_data_returns_empty_dict(self):
        assert parse_time_stats([("01000", "Table 'X'. Scan count 1...")]) == {}


class TestParseExecutionPlan:
    def test_memory_grant_extracted(self):
        result = parse_execution_plan(_PLAN_XML)
        assert result["memory_grant_kb"] == 1024

    def test_physical_operators_collected(self):
        result = parse_execution_plan(_PLAN_XML)
        assert "Clustered Index Scan" in result["physical_operators"]
        assert "Hash Match" in result["physical_operators"]
        assert "Sort" in result["physical_operators"]

    def test_physical_operators_are_unique(self):
        result = parse_execution_plan(_PLAN_XML)
        assert len(result["physical_operators"]) == len(set(result["physical_operators"]))

    def test_spill_warning_detected(self):
        result = parse_execution_plan(_PLAN_XML_WITH_SPILL)
        assert len(result["spill_warnings"]) == 1
        assert result["spill_warnings"][0]["SpillLevel"] == "1"

    def test_no_spill_warning_when_none(self):
        result = parse_execution_plan(_PLAN_XML)
        assert result["spill_warnings"] == []

    def test_none_input_returns_empty_dict(self):
        assert parse_execution_plan(None) == {}

    def test_empty_string_returns_empty_dict(self):
        assert parse_execution_plan("") == {}

    def test_invalid_xml_returns_empty_dict(self):
        assert parse_execution_plan("not valid xml <<") == {}

    def test_xml_with_declaration(self):
        xml_with_decl = '<?xml version="1.0" encoding="utf-16"?>' + _PLAN_XML
        result = parse_execution_plan(xml_with_decl)
        assert result["memory_grant_kb"] == 1024

    def test_runtime_stats_cpu_and_elapsed(self):
        result = parse_execution_plan(_PLAN_XML_WITH_RUNTIME)
        assert result["runtime_stats"]["cpu_time_ms"] == 15
        assert result["runtime_stats"]["elapsed_time_ms"] == 267

    def test_runtime_stats_io_summed_across_operators(self):
        result = parse_execution_plan(_PLAN_XML_WITH_RUNTIME)
        assert result["runtime_stats"]["logical_reads"] == 689 + 50
        assert result["runtime_stats"]["physical_reads"] == 5 + 2
        assert result["runtime_stats"]["read_ahead_reads"] == 10 + 0

    def test_runtime_stats_empty_when_no_counters(self):
        result = parse_execution_plan(_PLAN_XML)
        assert result["runtime_stats"] == {}
