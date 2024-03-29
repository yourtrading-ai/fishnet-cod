from typing import List, Optional

from fastapi import APIRouter, HTTPException

from ..common import OptionalWalletAuthDep
from ...core.model import Algorithm
from ..api_model import UploadAlgorithmRequest

router = APIRouter(
    prefix="/algorithms",
    tags=["algorithms"],
    responses={404: {"description": "Not found"}},
    dependencies=[OptionalWalletAuthDep],
)


@router.get("")
async def get_algorithms(
    name: Optional[str] = None,
    by: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[Algorithm]:
    """
    Get all algorithms filtered by `name` and/or owner (`by`). If no filters are given, all algorithms are returned.
    """
    params = {}
    if name:
        params["name"] = name
    if by:
        params["owner"] = by
    if params:
        return await Algorithm.filter(**params).page(page=page, page_size=page_size)
    return await Algorithm.fetch_objects().page(page=page, page_size=page_size)


@router.put("")
async def upload_algorithm(algorithm: UploadAlgorithmRequest) -> Algorithm:
    """
    Upload an algorithm.
    If an `item_hash` is provided, it will update the algorithm with that id.
    """
    if algorithm.item_hash is not None:
        old_algorithm = await Algorithm.fetch(algorithm.item_hash).first()
        if old_algorithm is not None:
            if old_algorithm.owner != algorithm.owner:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot overwrite algorithm that is not owned by you",
                )
            old_algorithm.name = algorithm.name
            old_algorithm.desc = algorithm.desc
            old_algorithm.code = algorithm.code
            return await old_algorithm.save()
    return await Algorithm(**algorithm.dict()).save()


@router.get("/{algorithm_id}")
async def get_algorithm(algorithm_id: str) -> Algorithm:
    algorithm = await Algorithm.fetch(algorithm_id).first()
    if algorithm is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    return algorithm
