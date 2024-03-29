from typing import Optional

import pandas as pd
from aleph_message.models import PostMessage
from pydantic import ValidationError

from .model import Algorithm, Dataset, Execution, ExecutionStatus, Result, Timeseries


async def try_get_execution_from_message(message: PostMessage) -> Optional[Execution]:
    execution: Optional[Execution]
    if message.content.type in ["Execution"]:
        execution = await Execution.from_post(message)
    else:  # amend
        try:
            execution = await Execution.fetch(message.content.ref).first()
        except ValidationError:
            return None
    if isinstance(execution, Execution):
        return execution
    return None


async def run_execution(
    execution: Execution, executor_vm: str = "test"
) -> Optional[Execution]:
    async def set_failed(execution, reason):
        execution.status = ExecutionStatus.FAILED
        result = await Result(
            executionID=execution.item_hash,
            data=reason,
            owner=execution.owner,
            executor_vm=executor_vm,
        ).save()
        execution.resultID = result.item_hash
        return await execution.save()

    try:
        assert isinstance(execution, Execution)
    except AssertionError:
        return await set_failed(execution, "Invalid execution message")

    if execution.status != ExecutionStatus.PENDING:
        return execution

    execution.status = ExecutionStatus.RUNNING
    await execution.save()

    try:
        try:
            algorithm = await Algorithm.fetch(execution.algorithmID).first()
            assert isinstance(algorithm, Algorithm)
        except (IndexError, AssertionError):
            return await set_failed(
                execution, f"Algorithm {execution.algorithmID} not found"
            )

        try:
            # replace all \n with actual newlines
            algorithm.code = algorithm.code.replace("\\n", "\n")
            print(algorithm.code)
            # TODO: Add caching of code compiles
            # TODO: Add constraints of globals
            code = compile(algorithm.code, str(algorithm.item_hash), "exec")
            exec(code)
        except Exception as e:
            return await set_failed(
                execution, f"Failed to parse algorithm code: {e} {algorithm.code}"
            )

        if "run" not in locals():
            return await set_failed(
                execution,
                "No run(df: DataFrame, params: Optional[dict]=None) function found",
            )

        try:
            dataset = await Dataset.fetch(execution.datasetID).first()
            assert isinstance(dataset, Dataset)
        except (IndexError, AssertionError):
            return await set_failed(
                execution, f"Dataset {execution.datasetID} not found"
            )

        timeseries = await Timeseries.fetch(dataset.timeseriesIDs).all()
        if len(timeseries) != len(dataset.timeseriesIDs):
            if len(timeseries) == 0:
                return await set_failed(
                    execution, f"Timeseries for dataset {dataset.item_hash} not found"
                )
            # TODO: handle missing timeseries
            # return await set_failed(
            #    execution,
            #    f"Timeseries incomplete: {len(timeseries)} out of {len(dataset.timeseriesIDs)} found",
            # )

        try:
            # parse all timeseries as series and join them into a dataframe
            df = pd.concat(
                [
                    pd.Series(
                        [x[1] for x in ts.data],
                        index=[x[0] for x in ts.data],
                        name=ts.name,
                    )
                    for ts in timeseries
                ],
                axis=1,
            )
        except Exception as e:
            return await set_failed(execution, f"Failed to create dataframe: {e}")

        try:
            assert "run" in locals()
            result = locals()["run"](df, params=execution.params)
        except Exception as e:
            return await set_failed(execution, f"Failed to run algorithm: {e}")

        result_message = await Result(
            executionID=str(execution.item_hash),
            data=str(result),
            owner=execution.owner,
            executor_vm=executor_vm,
        ).save()
        execution.status = ExecutionStatus.SUCCESS
        execution.resultID = result_message.item_hash
        await execution.save()
    except Exception as e:
        return await set_failed(execution, f"Unexpected error occurred: {e}")
    finally:
        del locals()["run"]
        return execution
