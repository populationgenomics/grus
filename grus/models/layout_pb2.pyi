from buf.validate import validate_pb2 as _validate_pb2
from grus.models import pedigree_pb2 as _pedigree_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class XSolver(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    X_SOLVER_UNSPECIFIED: _ClassVar[XSolver]
    X_SOLVER_Z3: _ClassVar[XSolver]
    X_SOLVER_HIGHS: _ClassVar[XSolver]

class CoupleLine(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COUPLE_LINE_UNSPECIFIED: _ClassVar[CoupleLine]
    COUPLE_LINE_SINGLE: _ClassVar[CoupleLine]
    COUPLE_LINE_DOUBLE: _ClassVar[CoupleLine]
X_SOLVER_UNSPECIFIED: XSolver
X_SOLVER_Z3: XSolver
X_SOLVER_HIGHS: XSolver
COUPLE_LINE_UNSPECIFIED: CoupleLine
COUPLE_LINE_SINGLE: CoupleLine
COUPLE_LINE_DOUBLE: CoupleLine

class PedigreeLayout(_message.Message):
    __slots__ = ("key", "placement", "deferred")
    KEY_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_FIELD_NUMBER: _ClassVar[int]
    DEFERRED_FIELD_NUMBER: _ClassVar[int]
    key: LayoutKey
    placement: Placement
    deferred: str
    def __init__(self, key: _Optional[_Union[LayoutKey, _Mapping]] = ..., placement: _Optional[_Union[Placement, _Mapping]] = ..., deferred: _Optional[str] = ...) -> None: ...

class LayoutKey(_message.Message):
    __slots__ = ("algorithm_version", "pedigree_digest", "geometry")
    ALGORITHM_VERSION_FIELD_NUMBER: _ClassVar[int]
    PEDIGREE_DIGEST_FIELD_NUMBER: _ClassVar[int]
    GEOMETRY_FIELD_NUMBER: _ClassVar[int]
    algorithm_version: int
    pedigree_digest: bytes
    geometry: LayoutGeometry
    def __init__(self, algorithm_version: _Optional[int] = ..., pedigree_digest: _Optional[bytes] = ..., geometry: _Optional[_Union[LayoutGeometry, _Mapping]] = ...) -> None: ...

class LayoutGeometry(_message.Message):
    __slots__ = ("couple_gap", "sib_gap", "label_size", "label_box_width", "x_unit", "x_solver")
    COUPLE_GAP_FIELD_NUMBER: _ClassVar[int]
    SIB_GAP_FIELD_NUMBER: _ClassVar[int]
    LABEL_SIZE_FIELD_NUMBER: _ClassVar[int]
    LABEL_BOX_WIDTH_FIELD_NUMBER: _ClassVar[int]
    X_UNIT_FIELD_NUMBER: _ClassVar[int]
    X_SOLVER_FIELD_NUMBER: _ClassVar[int]
    couple_gap: float
    sib_gap: float
    label_size: float
    label_box_width: float
    x_unit: float
    x_solver: XSolver
    def __init__(self, couple_gap: _Optional[float] = ..., sib_gap: _Optional[float] = ..., label_size: _Optional[float] = ..., label_box_width: _Optional[float] = ..., x_unit: _Optional[float] = ..., x_solver: _Optional[_Union[XSolver, str]] = ...) -> None: ...

class Placement(_message.Message):
    __slots__ = ("first_generation", "rows", "founder_sibships", "routed")
    FIRST_GENERATION_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    FOUNDER_SIBSHIPS_FIELD_NUMBER: _ClassVar[int]
    ROUTED_FIELD_NUMBER: _ClassVar[int]
    first_generation: int
    rows: _containers.RepeatedCompositeFieldContainer[Row]
    founder_sibships: _containers.RepeatedCompositeFieldContainer[FounderSibship]
    routed: _containers.RepeatedCompositeFieldContainer[RoutedMating]
    def __init__(self, first_generation: _Optional[int] = ..., rows: _Optional[_Iterable[_Union[Row, _Mapping]]] = ..., founder_sibships: _Optional[_Iterable[_Union[FounderSibship, _Mapping]]] = ..., routed: _Optional[_Iterable[_Union[RoutedMating, _Mapping]]] = ...) -> None: ...

class Row(_message.Message):
    __slots__ = ("cells",)
    CELLS_FIELD_NUMBER: _ClassVar[int]
    cells: _containers.RepeatedCompositeFieldContainer[Cell]
    def __init__(self, cells: _Optional[_Iterable[_Union[Cell, _Mapping]]] = ...) -> None: ...

class Cell(_message.Message):
    __slots__ = ("individual", "ghost", "phantom", "pass_through", "x", "parent_column", "lone", "couple_right", "twin_right", "childless")
    INDIVIDUAL_FIELD_NUMBER: _ClassVar[int]
    GHOST_FIELD_NUMBER: _ClassVar[int]
    PHANTOM_FIELD_NUMBER: _ClassVar[int]
    PASS_THROUGH_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    PARENT_COLUMN_FIELD_NUMBER: _ClassVar[int]
    LONE_FIELD_NUMBER: _ClassVar[int]
    COUPLE_RIGHT_FIELD_NUMBER: _ClassVar[int]
    TWIN_RIGHT_FIELD_NUMBER: _ClassVar[int]
    CHILDLESS_FIELD_NUMBER: _ClassVar[int]
    individual: _pedigree_pb2.Position
    ghost: Ghost
    phantom: Phantom
    pass_through: PassThrough
    x: float
    parent_column: int
    lone: bool
    couple_right: CoupleLine
    twin_right: _pedigree_pb2.ZygosityType
    childless: _pedigree_pb2.Childlessness
    def __init__(self, individual: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ..., ghost: _Optional[_Union[Ghost, _Mapping]] = ..., phantom: _Optional[_Union[Phantom, _Mapping]] = ..., pass_through: _Optional[_Union[PassThrough, _Mapping]] = ..., x: _Optional[float] = ..., parent_column: _Optional[int] = ..., lone: _Optional[bool] = ..., couple_right: _Optional[_Union[CoupleLine, str]] = ..., twin_right: _Optional[_Union[_pedigree_pb2.ZygosityType, str]] = ..., childless: _Optional[_Union[_pedigree_pb2.Childlessness, str]] = ...) -> None: ...

class Ghost(_message.Message):
    __slots__ = ("real", "partner")
    REAL_FIELD_NUMBER: _ClassVar[int]
    PARTNER_FIELD_NUMBER: _ClassVar[int]
    real: _pedigree_pb2.Position
    partner: _pedigree_pb2.Position
    def __init__(self, real: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ..., partner: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ...) -> None: ...

class Phantom(_message.Message):
    __slots__ = ("parent", "first_child")
    PARENT_FIELD_NUMBER: _ClassVar[int]
    FIRST_CHILD_FIELD_NUMBER: _ClassVar[int]
    parent: _pedigree_pb2.Position
    first_child: _pedigree_pb2.Position
    def __init__(self, parent: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ..., first_child: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ...) -> None: ...

class PassThrough(_message.Message):
    __slots__ = ("first_child",)
    FIRST_CHILD_FIELD_NUMBER: _ClassVar[int]
    first_child: _pedigree_pb2.Position
    def __init__(self, first_child: _Optional[_Union[_pedigree_pb2.Position, _Mapping]] = ...) -> None: ...

class FounderSibship(_message.Message):
    __slots__ = ("row", "columns")
    ROW_FIELD_NUMBER: _ClassVar[int]
    COLUMNS_FIELD_NUMBER: _ClassVar[int]
    row: int
    columns: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, row: _Optional[int] = ..., columns: _Optional[_Iterable[int]] = ...) -> None: ...

class RoutedMating(_message.Message):
    __slots__ = ("a", "b", "consanguineous", "children")
    A_FIELD_NUMBER: _ClassVar[int]
    B_FIELD_NUMBER: _ClassVar[int]
    CONSANGUINEOUS_FIELD_NUMBER: _ClassVar[int]
    CHILDREN_FIELD_NUMBER: _ClassVar[int]
    a: CellRef
    b: CellRef
    consanguineous: bool
    children: _containers.RepeatedCompositeFieldContainer[CellRef]
    def __init__(self, a: _Optional[_Union[CellRef, _Mapping]] = ..., b: _Optional[_Union[CellRef, _Mapping]] = ..., consanguineous: _Optional[bool] = ..., children: _Optional[_Iterable[_Union[CellRef, _Mapping]]] = ...) -> None: ...

class CellRef(_message.Message):
    __slots__ = ("row", "column")
    ROW_FIELD_NUMBER: _ClassVar[int]
    COLUMN_FIELD_NUMBER: _ClassVar[int]
    row: int
    column: int
    def __init__(self, row: _Optional[int] = ..., column: _Optional[int] = ...) -> None: ...
