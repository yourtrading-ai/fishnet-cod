from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi_walletauth import JWTWalletAuthDep
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from ...core.model import Timeseries
from ..api_model import ColumnNameType, TimeseriesWithData, UploadTimeseriesRequest
from ..controllers import (
    check_access,
    create_csv_streaming_response,
    get_harmonized_timeseries_df,
    increase_user_downloads,
    load_data_df,
    upsert_timeseries,
)
from ..utils import determine_decimal_places

router = APIRouter(
    prefix="/timeseries",
    tags=["timeseries"],
    responses={404: {"description": "Not found"}},
)


@router.put("")
async def upload_timeseries(
    req: UploadTimeseriesRequest,
    user: JWTWalletAuthDep,
) -> List[Timeseries]:
    """
    Upload a list of timeseries. If the passed timeseries has an `item_hash` and it already exists,
    it will be overwritten. If the timeseries does not exist, it will be created.
    A list of the created/updated timeseries is returned.
    """
    updated_timeseries, created_timeseries = await upsert_timeseries(
        req.timeseries, user
    )
    return updated_timeseries + created_timeseries


@router.post("/csv")
async def preprocess_timeseries_csv(
    user: JWTWalletAuthDep,
    data_file: UploadFile = File(...),
) -> List[TimeseriesWithData]:
    """
    Preprocess a csv file with timeseries data. The csv file must have a header row with the following columns:
    `item_hash`, `name`, `desc`, `data`. The `data` column must contain a json string with the timeseries data.
    The returned list of timeseries will not be persisted yet.
    """
    df = load_data_df(data_file)
    # create a timeseries object for each column
    timestamps = [dt.timestamp() for dt in df.index.to_pydatetime().tolist()]
    timeseries = []
    for col in df.columns:
        try:
            data = [(timestamps[i], value) for i, value in enumerate(df[col].tolist())]
            # calculate a good number to round with, based on the min and max values
            decimals = determine_decimal_places(df[col])

            timeseries.append(
                TimeseriesWithData(
                    item_hash=None,
                    name=col,
                    desc=None,
                    data=data,
                    owner=user.address,
                    min=df[col].min().round(decimals),
                    max=df[col].max().round(decimals),
                    avg=df[col].mean().round(decimals),
                    std=df[col].std().round(decimals),
                    median=df[col].median().round(decimals),
                    earliest=timestamps[-1],
                    latest=timestamps[0],
                )
            )
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid data encountered in column {col}: {data}",
            )
    return timeseries


@router.get("/json")
async def download_timeseries_json(
    timeseriesIDs: str,
    user: JWTWalletAuthDep,
) -> List[TimeseriesWithData]:
    """
    Download a list of timeseries with their data. The data is attached to each timeseries object.
    """
    timeseries_ids = timeseriesIDs.split(",")
    timeseries = await Timeseries.fetch(timeseries_ids).all()
    await check_access(timeseries, user)

    df = get_harmonized_timeseries_df(timeseries)
    return [
        TimeseriesWithData(
            **ts.dict(),
            data=[
                (int(dt.timestamp()), value)
                for dt, value in df[ts.item_hash].iteritems()
            ],
        )
        for ts in timeseries
    ]


@router.get("/csv")
async def download_timeseries_csv(
    timeseriesIDs: str,
    user: JWTWalletAuthDep,
    column_names: ColumnNameType = ColumnNameType.name,
    compression: bool = False,
) -> StreamingResponse:
    """
    Download a csv file with timeseries data. The csv file will have a `timestamp` column and a column for each
    timeseries. The column name of each timeseries is either the `item_hash` or the `name` of the timeseries,
    depending on the `column_names` parameter.

    If timeseries timestamps do not align perfectly, the missing values will be filled with the last known value.

    If `compression` is set to `True`, the csv file will be compressed with gzip.
    """
    timeseries_ids = timeseriesIDs.split(",")
    timeseries = await Timeseries.fetch(timeseries_ids).all()
    await check_access(timeseries, user)

    df = get_harmonized_timeseries_df(timeseries, column_names=column_names)

    # increase download count
    owners = list({ts.owner for ts in timeseries})
    await increase_user_downloads(owners)

    response = await create_csv_streaming_response(df, compression)

    # Generate a hash from the timeseries IDs and use it as the filename
    filename = hash("".join(timeseries_ids))
    response.headers["Content-Disposition"] = f"attachment; filename={filename}.csv"

    return response
