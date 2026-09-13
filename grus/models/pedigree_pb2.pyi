from buf.validate import validate_pb2 as _validate_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Gender(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GENDER_UNSPECIFIED: _ClassVar[Gender]
    GENDER_MAN: _ClassVar[Gender]
    GENDER_WOMAN: _ClassVar[Gender]
    GENDER_NONBINARY: _ClassVar[Gender]
    GENDER_UNKNOWN: _ClassVar[Gender]

class SexAssignedAtBirth(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEX_ASSIGNED_AT_BIRTH_UNSPECIFIED: _ClassVar[SexAssignedAtBirth]
    SEX_ASSIGNED_AT_BIRTH_MALE: _ClassVar[SexAssignedAtBirth]
    SEX_ASSIGNED_AT_BIRTH_FEMALE: _ClassVar[SexAssignedAtBirth]
    SEX_ASSIGNED_AT_BIRTH_UNASSIGNED: _ClassVar[SexAssignedAtBirth]

class ConditionStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONDITION_STATUS_UNSPECIFIED: _ClassVar[ConditionStatus]
    CONDITION_STATUS_UNAFFECTED: _ClassVar[ConditionStatus]
    CONDITION_STATUS_AFFECTED: _ClassVar[ConditionStatus]
    CONDITION_STATUS_CARRIER: _ClassVar[ConditionStatus]
    CONDITION_STATUS_PRESYMPTOMATIC: _ClassVar[ConditionStatus]
    CONDITION_STATUS_UNKNOWN: _ClassVar[ConditionStatus]

class Inheritance(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    INHERITANCE_UNSPECIFIED: _ClassVar[Inheritance]
    INHERITANCE_AUTOSOMAL_DOMINANT: _ClassVar[Inheritance]
    INHERITANCE_AUTOSOMAL_RECESSIVE: _ClassVar[Inheritance]
    INHERITANCE_X_LINKED_DOMINANT: _ClassVar[Inheritance]
    INHERITANCE_X_LINKED_RECESSIVE: _ClassVar[Inheritance]
    INHERITANCE_Y_LINKED: _ClassVar[Inheritance]
    INHERITANCE_MITOCHONDRIAL: _ClassVar[Inheritance]

class ReproductiveOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REPRODUCTIVE_OUTCOME_UNSPECIFIED: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_LIVE: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_PREGNANCY: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_STILLBIRTH: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_MISCARRIAGE: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_TERMINATION: _ClassVar[ReproductiveOutcome]
    REPRODUCTIVE_OUTCOME_ECTOPIC: _ClassVar[ReproductiveOutcome]

class ReproductiveRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REPRODUCTIVE_ROLE_UNSPECIFIED: _ClassVar[ReproductiveRole]
    REPRODUCTIVE_ROLE_SELF: _ClassVar[ReproductiveRole]
    REPRODUCTIVE_ROLE_GAMETE_DONOR: _ClassVar[ReproductiveRole]
    REPRODUCTIVE_ROLE_GESTATIONAL_CARRIER: _ClassVar[ReproductiveRole]

class AnnotationType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANNOTATION_TYPE_UNSPECIFIED: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_GENOTYPE: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_VARIANT: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_PHENOTYPE: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_AGE: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_GESTATIONAL_AGE: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_KARYOTYPE: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_MEASUREMENT: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_PRONOUN: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_CHOSEN_NAME: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_OTHER: _ClassVar[AnnotationType]
    ANNOTATION_TYPE_AGE_AT_DEATH: _ClassVar[AnnotationType]

class RelationshipStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RELATIONSHIP_STATUS_UNSPECIFIED: _ClassVar[RelationshipStatus]
    RELATIONSHIP_STATUS_CURRENT: _ClassVar[RelationshipStatus]
    RELATIONSHIP_STATUS_SEPARATED: _ClassVar[RelationshipStatus]
    RELATIONSHIP_STATUS_DIVORCED: _ClassVar[RelationshipStatus]

class Childlessness(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHILDLESSNESS_UNSPECIFIED: _ClassVar[Childlessness]
    CHILDLESSNESS_NONE: _ClassVar[Childlessness]
    CHILDLESSNESS_BY_CHOICE: _ClassVar[Childlessness]
    CHILDLESSNESS_INFERTILITY: _ClassVar[Childlessness]

class ZygosityType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ZYGOSITY_TYPE_UNSPECIFIED: _ClassVar[ZygosityType]
    ZYGOSITY_TYPE_MONOZYGOTIC: _ClassVar[ZygosityType]
    ZYGOSITY_TYPE_DIZYGOTIC: _ClassVar[ZygosityType]
    ZYGOSITY_TYPE_UNKNOWN: _ClassVar[ZygosityType]
    ZYGOSITY_TYPE_TRIZYGOTIC: _ClassVar[ZygosityType]

class Parentage(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PARENTAGE_UNSPECIFIED: _ClassVar[Parentage]
    PARENTAGE_BIOLOGICAL: _ClassVar[Parentage]
    PARENTAGE_ADOPTIVE: _ClassVar[Parentage]
    PARENTAGE_DONOR: _ClassVar[Parentage]

class Adoption(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ADOPTION_UNSPECIFIED: _ClassVar[Adoption]
    ADOPTION_NONE: _ClassVar[Adoption]
    ADOPTION_IN: _ClassVar[Adoption]
    ADOPTION_OUT: _ClassVar[Adoption]
    ADOPTION_BY_RELATIVE: _ClassVar[Adoption]

class LabelKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LABEL_KIND_UNSPECIFIED: _ClassVar[LabelKind]
    LABEL_KIND_FAMILY: _ClassVar[LabelKind]
    LABEL_KIND_PANEL: _ClassVar[LabelKind]
    LABEL_KIND_GENE: _ClassVar[LabelKind]
    LABEL_KIND_PHENOTYPE: _ClassVar[LabelKind]
    LABEL_KIND_OTHER: _ClassVar[LabelKind]
GENDER_UNSPECIFIED: Gender
GENDER_MAN: Gender
GENDER_WOMAN: Gender
GENDER_NONBINARY: Gender
GENDER_UNKNOWN: Gender
SEX_ASSIGNED_AT_BIRTH_UNSPECIFIED: SexAssignedAtBirth
SEX_ASSIGNED_AT_BIRTH_MALE: SexAssignedAtBirth
SEX_ASSIGNED_AT_BIRTH_FEMALE: SexAssignedAtBirth
SEX_ASSIGNED_AT_BIRTH_UNASSIGNED: SexAssignedAtBirth
CONDITION_STATUS_UNSPECIFIED: ConditionStatus
CONDITION_STATUS_UNAFFECTED: ConditionStatus
CONDITION_STATUS_AFFECTED: ConditionStatus
CONDITION_STATUS_CARRIER: ConditionStatus
CONDITION_STATUS_PRESYMPTOMATIC: ConditionStatus
CONDITION_STATUS_UNKNOWN: ConditionStatus
INHERITANCE_UNSPECIFIED: Inheritance
INHERITANCE_AUTOSOMAL_DOMINANT: Inheritance
INHERITANCE_AUTOSOMAL_RECESSIVE: Inheritance
INHERITANCE_X_LINKED_DOMINANT: Inheritance
INHERITANCE_X_LINKED_RECESSIVE: Inheritance
INHERITANCE_Y_LINKED: Inheritance
INHERITANCE_MITOCHONDRIAL: Inheritance
REPRODUCTIVE_OUTCOME_UNSPECIFIED: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_LIVE: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_PREGNANCY: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_STILLBIRTH: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_MISCARRIAGE: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_TERMINATION: ReproductiveOutcome
REPRODUCTIVE_OUTCOME_ECTOPIC: ReproductiveOutcome
REPRODUCTIVE_ROLE_UNSPECIFIED: ReproductiveRole
REPRODUCTIVE_ROLE_SELF: ReproductiveRole
REPRODUCTIVE_ROLE_GAMETE_DONOR: ReproductiveRole
REPRODUCTIVE_ROLE_GESTATIONAL_CARRIER: ReproductiveRole
ANNOTATION_TYPE_UNSPECIFIED: AnnotationType
ANNOTATION_TYPE_GENOTYPE: AnnotationType
ANNOTATION_TYPE_VARIANT: AnnotationType
ANNOTATION_TYPE_PHENOTYPE: AnnotationType
ANNOTATION_TYPE_AGE: AnnotationType
ANNOTATION_TYPE_GESTATIONAL_AGE: AnnotationType
ANNOTATION_TYPE_KARYOTYPE: AnnotationType
ANNOTATION_TYPE_MEASUREMENT: AnnotationType
ANNOTATION_TYPE_PRONOUN: AnnotationType
ANNOTATION_TYPE_CHOSEN_NAME: AnnotationType
ANNOTATION_TYPE_OTHER: AnnotationType
ANNOTATION_TYPE_AGE_AT_DEATH: AnnotationType
RELATIONSHIP_STATUS_UNSPECIFIED: RelationshipStatus
RELATIONSHIP_STATUS_CURRENT: RelationshipStatus
RELATIONSHIP_STATUS_SEPARATED: RelationshipStatus
RELATIONSHIP_STATUS_DIVORCED: RelationshipStatus
CHILDLESSNESS_UNSPECIFIED: Childlessness
CHILDLESSNESS_NONE: Childlessness
CHILDLESSNESS_BY_CHOICE: Childlessness
CHILDLESSNESS_INFERTILITY: Childlessness
ZYGOSITY_TYPE_UNSPECIFIED: ZygosityType
ZYGOSITY_TYPE_MONOZYGOTIC: ZygosityType
ZYGOSITY_TYPE_DIZYGOTIC: ZygosityType
ZYGOSITY_TYPE_UNKNOWN: ZygosityType
ZYGOSITY_TYPE_TRIZYGOTIC: ZygosityType
PARENTAGE_UNSPECIFIED: Parentage
PARENTAGE_BIOLOGICAL: Parentage
PARENTAGE_ADOPTIVE: Parentage
PARENTAGE_DONOR: Parentage
ADOPTION_UNSPECIFIED: Adoption
ADOPTION_NONE: Adoption
ADOPTION_IN: Adoption
ADOPTION_OUT: Adoption
ADOPTION_BY_RELATIVE: Adoption
LABEL_KIND_UNSPECIFIED: LabelKind
LABEL_KIND_FAMILY: LabelKind
LABEL_KIND_PANEL: LabelKind
LABEL_KIND_GENE: LabelKind
LABEL_KIND_PHENOTYPE: LabelKind
LABEL_KIND_OTHER: LabelKind

class Position(_message.Message):
    __slots__ = ("generation", "index")
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    generation: int
    index: int
    def __init__(self, generation: _Optional[int] = ..., index: _Optional[int] = ...) -> None: ...

class Condition(_message.Message):
    __slots__ = ("name", "status", "inheritance", "onset_age")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    INHERITANCE_FIELD_NUMBER: _ClassVar[int]
    ONSET_AGE_FIELD_NUMBER: _ClassVar[int]
    name: str
    status: ConditionStatus
    inheritance: Inheritance
    onset_age: str
    def __init__(self, name: _Optional[str] = ..., status: _Optional[_Union[ConditionStatus, str]] = ..., inheritance: _Optional[_Union[Inheritance, str]] = ..., onset_age: _Optional[str] = ...) -> None: ...

class Individual(_message.Message):
    __slots__ = ("generation", "index", "gender", "sex_assigned_at_birth", "conditions", "deceased", "proband", "consultand", "reproductive_outcome", "reproductive_role", "count", "count_unspecified", "annotations", "external_id", "documented_evaluation")
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    GENDER_FIELD_NUMBER: _ClassVar[int]
    SEX_ASSIGNED_AT_BIRTH_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    DECEASED_FIELD_NUMBER: _ClassVar[int]
    PROBAND_FIELD_NUMBER: _ClassVar[int]
    CONSULTAND_FIELD_NUMBER: _ClassVar[int]
    REPRODUCTIVE_OUTCOME_FIELD_NUMBER: _ClassVar[int]
    REPRODUCTIVE_ROLE_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    COUNT_UNSPECIFIED_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENTED_EVALUATION_FIELD_NUMBER: _ClassVar[int]
    generation: int
    index: int
    gender: Gender
    sex_assigned_at_birth: SexAssignedAtBirth
    conditions: _containers.RepeatedCompositeFieldContainer[Condition]
    deceased: bool
    proband: bool
    consultand: bool
    reproductive_outcome: ReproductiveOutcome
    reproductive_role: ReproductiveRole
    count: int
    count_unspecified: bool
    annotations: _containers.RepeatedCompositeFieldContainer[Annotation]
    external_id: str
    documented_evaluation: bool
    def __init__(self, generation: _Optional[int] = ..., index: _Optional[int] = ..., gender: _Optional[_Union[Gender, str]] = ..., sex_assigned_at_birth: _Optional[_Union[SexAssignedAtBirth, str]] = ..., conditions: _Optional[_Iterable[_Union[Condition, _Mapping]]] = ..., deceased: _Optional[bool] = ..., proband: _Optional[bool] = ..., consultand: _Optional[bool] = ..., reproductive_outcome: _Optional[_Union[ReproductiveOutcome, str]] = ..., reproductive_role: _Optional[_Union[ReproductiveRole, str]] = ..., count: _Optional[int] = ..., count_unspecified: _Optional[bool] = ..., annotations: _Optional[_Iterable[_Union[Annotation, _Mapping]]] = ..., external_id: _Optional[str] = ..., documented_evaluation: _Optional[bool] = ...) -> None: ...

class Annotation(_message.Message):
    __slots__ = ("text", "type")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    text: str
    type: AnnotationType
    def __init__(self, text: _Optional[str] = ..., type: _Optional[_Union[AnnotationType, str]] = ...) -> None: ...

class Mating(_message.Message):
    __slots__ = ("partner_a", "partner_b", "consanguineous", "status", "childlessness", "offspring", "annotations")
    PARTNER_A_FIELD_NUMBER: _ClassVar[int]
    PARTNER_B_FIELD_NUMBER: _ClassVar[int]
    CONSANGUINEOUS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CHILDLESSNESS_FIELD_NUMBER: _ClassVar[int]
    OFFSPRING_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    partner_a: Position
    partner_b: Position
    consanguineous: bool
    status: RelationshipStatus
    childlessness: Childlessness
    offspring: _containers.RepeatedCompositeFieldContainer[Offspring]
    annotations: _containers.RepeatedCompositeFieldContainer[Annotation]
    def __init__(self, partner_a: _Optional[_Union[Position, _Mapping]] = ..., partner_b: _Optional[_Union[Position, _Mapping]] = ..., consanguineous: _Optional[bool] = ..., status: _Optional[_Union[RelationshipStatus, str]] = ..., childlessness: _Optional[_Union[Childlessness, str]] = ..., offspring: _Optional[_Iterable[_Union[Offspring, _Mapping]]] = ..., annotations: _Optional[_Iterable[_Union[Annotation, _Mapping]]] = ...) -> None: ...

class Offspring(_message.Message):
    __slots__ = ("child", "twin_group", "twin_type", "parentage", "adoption")
    CHILD_FIELD_NUMBER: _ClassVar[int]
    TWIN_GROUP_FIELD_NUMBER: _ClassVar[int]
    TWIN_TYPE_FIELD_NUMBER: _ClassVar[int]
    PARENTAGE_FIELD_NUMBER: _ClassVar[int]
    ADOPTION_FIELD_NUMBER: _ClassVar[int]
    child: Position
    twin_group: int
    twin_type: ZygosityType
    parentage: Parentage
    adoption: Adoption
    def __init__(self, child: _Optional[_Union[Position, _Mapping]] = ..., twin_group: _Optional[int] = ..., twin_type: _Optional[_Union[ZygosityType, str]] = ..., parentage: _Optional[_Union[Parentage, str]] = ..., adoption: _Optional[_Union[Adoption, str]] = ...) -> None: ...

class Label(_message.Message):
    __slots__ = ("text", "kind")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    text: str
    kind: LabelKind
    def __init__(self, text: _Optional[str] = ..., kind: _Optional[_Union[LabelKind, str]] = ...) -> None: ...

class Pedigree(_message.Message):
    __slots__ = ("id", "labels", "individuals", "matings", "provenance")
    ID_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    INDIVIDUALS_FIELD_NUMBER: _ClassVar[int]
    MATINGS_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    labels: _containers.RepeatedCompositeFieldContainer[Label]
    individuals: _containers.RepeatedCompositeFieldContainer[Individual]
    matings: _containers.RepeatedCompositeFieldContainer[Mating]
    provenance: Provenance
    def __init__(self, id: _Optional[str] = ..., labels: _Optional[_Iterable[_Union[Label, _Mapping]]] = ..., individuals: _Optional[_Iterable[_Union[Individual, _Mapping]]] = ..., matings: _Optional[_Iterable[_Union[Mating, _Mapping]]] = ..., provenance: _Optional[_Union[Provenance, _Mapping]] = ...) -> None: ...

class PedigreeSet(_message.Message):
    __slots__ = ("pedigrees", "provenance")
    PEDIGREES_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    pedigrees: _containers.RepeatedCompositeFieldContainer[Pedigree]
    provenance: Provenance
    def __init__(self, pedigrees: _Optional[_Iterable[_Union[Pedigree, _Mapping]]] = ..., provenance: _Optional[_Union[Provenance, _Mapping]] = ...) -> None: ...

class Provenance(_message.Message):
    __slots__ = ("figure_uri", "doi", "extractor_model", "source_format")
    FIGURE_URI_FIELD_NUMBER: _ClassVar[int]
    DOI_FIELD_NUMBER: _ClassVar[int]
    EXTRACTOR_MODEL_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FORMAT_FIELD_NUMBER: _ClassVar[int]
    figure_uri: str
    doi: str
    extractor_model: str
    source_format: str
    def __init__(self, figure_uri: _Optional[str] = ..., doi: _Optional[str] = ..., extractor_model: _Optional[str] = ..., source_format: _Optional[str] = ...) -> None: ...
