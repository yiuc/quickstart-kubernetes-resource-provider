# DO NOT modify this file by hand, changes will be overwritten
import sys
from dataclasses import dataclass
from inspect import getmembers, isclass
from typing import (
    AbstractSet,
    Any,
    Generic,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Type,
    TypeVar,
)

from cloudformation_cli_python_lib.interface import (
    BaseModel,
    BaseResourceHandlerRequest,
)
from cloudformation_cli_python_lib.recast import recast_object
from cloudformation_cli_python_lib.utils import deserialize_list

T = TypeVar("T")


def set_or_none(value: Optional[Sequence[T]]) -> Optional[AbstractSet[T]]:
    if value:
        return set(value)
    return None


@dataclass
class ResourceHandlerRequest(BaseResourceHandlerRequest):
    # pylint: disable=invalid-name
    desiredResourceState: Optional["ResourceModel"]
    previousResourceState: Optional["ResourceModel"]


@dataclass
class ResourceModel(BaseModel):
    ClusterName: Optional[str]
    Namespace: Optional[str]
    Manifest: Optional[str]
    Url: Optional[str]
    name: Optional[str]
    resourceVersion: Optional[str]
    selfLink: Optional[str]
    uid: Optional[str]

    @classmethod
    def _deserialize(
        cls: Type["_ResourceModel"],
        json_data: Optional[Mapping[str, Any]],
    ) -> Optional["_ResourceModel"]:
        if not json_data:
            return None
        dataclasses = {n: o for n, o in getmembers(sys.modules[__name__]) if isclass(o)}
        recast_object(cls, json_data, dataclasses)
        return cls(
            ClusterName=json_data.get("ClusterName"),
            Namespace=json_data.get("Namespace"),
            Manifest=json_data.get("Manifest"),
            Url=json_data.get("Url"),
            name=json_data.get("name"),
            resourceVersion=json_data.get("resourceVersion"),
            selfLink=json_data.get("selfLink"),
            uid=json_data.get("uid"),
        )


# work around possible type aliasing issues when variable has same name as a model
_ResourceModel = ResourceModel


