from dataclasses import dataclass, field
from typing import Optional, List, Any


@dataclass
class AskRequest:
    question: str
    mode: str = "standard"


@dataclass
class DebugEntry:
    label: str
    content: str


@dataclass
class ChartSpec:
    type: str                    # bar, line, pie, donut, scatter, stacked_bar
    title: str
    labels: List[str]
    datasets: List[dict]         # [{label, data, color}]
    x_label: str = ""
    y_label: str = ""


@dataclass
class AskResponse:
    answer: str
    debug: List[DebugEntry] = field(default_factory=list)
    chart: Optional[ChartSpec] = None

    def to_dict(self) -> dict:
        result = {
            "answer": self.answer,
            "debug": [{"label": d.label, "content": d.content} for d in self.debug],
        }
        if self.chart:
            result["chart"] = {
                "type":     self.chart.type,
                "title":    self.chart.title,
                "labels":   self.chart.labels,
                "datasets": self.chart.datasets,
                "x_label":  self.chart.x_label,
                "y_label":  self.chart.y_label,
            }
        return result


@dataclass
class UploadResponse:
    ok: bool
    rows: int
    cols: int
    columns: List[dict]
    preview: List[dict]
    filename: str
    encoding: str = ""
    sheets: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok":       self.ok,
            "rows":     self.rows,
            "cols":     self.cols,
            "columns":  self.columns,
            "preview":  self.preview,
            "filename": self.filename,
            "encoding": self.encoding,
            "sheets":   self.sheets,
            "warnings": self.warnings,
        }
