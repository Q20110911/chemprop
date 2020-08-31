from collections import defaultdict
import logging
from typing import Dict, List, Tuple

import numpy as np

from .predict import predict
from chemprop.data import MoleculeDataLoader, StandardScaler
from chemprop.models import MoleculeModel
from chemprop.utils import get_metric_func


def select_valid_values(preds: List[List[float]],
                        targets: List[List[float]],
                        num_tasks: int,
                        metric_by_row: bool = False) -> Tuple[List[List[float]], List[List[float]]]:
    """
    Selects valid preds and targets corresponding to those where the target is not None.

    :param preds: A list of lists of shape :code:`(data_size, num_tasks)` with model predictions.
    :param targets: A list of lists of shape :code:`(data_size, num_tasks)` with targets.
    :param num_tasks: Number of tasks.
    :param metric_by_row: Whether to apply the metric to each row (and then average) rather than to each column.
    :return: A tuple of preds and targets with None entries removed.
    """
    num_values = len(preds) if metric_by_row else num_tasks

    # Filter out empty targets
    # valid_preds and valid_targets have shape (num_values, data_size)
    valid_preds = [[] for _ in range(num_values)]
    valid_targets = [[] for _ in range(num_values)]
    for i in range(num_tasks):
        for j in range(len(preds)):
            if targets[j][i] is not None:  # Skip those without targets
                index = j if metric_by_row else i
                valid_preds[index].append(preds[j][i])
                valid_targets[index].append(targets[j][i])

    return valid_preds, valid_targets


def _evaluate_predictions(preds: List[List[float]],
                          targets: List[List[float]],
                          num_tasks: int,
                          metrics: List[str],
                          dataset_type: str,
                          metric_by_row: bool = False,
                          logger: logging.Logger = None) -> Dict[str, List[float]]:
    """
    Evaluates predictions using a metric function after filtering out invalid targets.

    :param preds: A list of lists of shape :code:`(data_size, num_tasks)` with model predictions.
    :param targets: A list of lists of shape :code:`(data_size, num_tasks)` with targets.
    :param num_tasks: Number of tasks.
    :param metrics: A list of names of metric functions.
    :param dataset_type: Dataset type.
    :param metric_by_row: Whether to apply the metric to each row (and then average) rather than to each column.
    :param logger: A logger to record output.
    :return: A dictionary mapping each metric in :code:`metrics` to a list of values for each task.
    """
    if len(preds) == 0:
        return {metric: [float('nan')] * num_tasks for metric in metrics}

    info = logger.info if logger is not None else print

    metric_to_func = {f'{metric}{"-by-row" if metric_by_row else ""}': get_metric_func(metric) for metric in metrics}

    # Filter out empty targets
    valid_preds, valid_targets = select_valid_values(
        preds=preds,
        targets=targets,
        num_tasks=num_tasks,
        metric_by_row=metric_by_row
    )

    # Compute metric
    results = defaultdict(list)
    for i in range(len(preds)):
        # # Skip if all targets or preds are identical, otherwise we'll crash during classification
        if dataset_type == 'classification':
            nan = False
            if all(target == 0 for target in valid_targets[i]) or all(target == 1 for target in valid_targets[i]):
                nan = True
                if not metric_by_row:
                    info('Warning: Found a task with targets all 0s or all 1s')
            if all(pred == 0 for pred in valid_preds[i]) or all(pred == 1 for pred in valid_preds[i]):
                nan = True
                if not metric_by_row:
                    info('Warning: Found a task with predictions all 0s or all 1s')

            if nan:
                for metric in metric_to_func:
                    results[metric].append(float('nan'))
                continue

        if len(valid_targets[i]) == 0:
            continue

        for metric, metric_func in metric_to_func.items():
            if dataset_type == 'multiclass':
                results[metric].append(metric_func(valid_targets[i], valid_preds[i],
                                                   labels=list(range(len(valid_preds[i][0])))))
            else:
                results[metric].append(metric_func(valid_targets[i], valid_preds[i]))

    results = dict(results)

    # Average metric across molecules
    if metric_by_row:
        for metric, values in results.items():
            results[metric] = [np.nanmean(values)]

    return results


def evaluate_predictions(preds: List[List[float]],
                         targets: List[List[float]],
                         num_tasks: int,
                         metrics: List[str],
                         dataset_type: str,
                         metric_by_row: bool = False,
                         logger: logging.Logger = None) -> Dict[str, List[float]]:
    """
    Wrapper around :func:`_evaluate_predictions` which valuates predictions using a metric function after filtering out invalid targets.

    :param preds: A list of lists of shape :code:`(data_size, num_tasks)` with model predictions.
    :param targets: A list of lists of shape :code:`(data_size, num_tasks)` with targets.
    :param num_tasks: Number of tasks.
    :param metrics: A list of names of metric functions.
    :param dataset_type: Dataset type.
    :param metric_by_row: Whether to apply the metric to each row (and then average) rather than to each column.
    :param logger: A logger to record output.
    :return: A dictionary mapping each metric in :code:`metrics` to a list of values for each task.
    """
    results = _evaluate_predictions(
        preds=preds,
        targets=targets,
        num_tasks=num_tasks,
        metrics=metrics,
        dataset_type=dataset_type,
        metric_by_row=False,
        logger=logger
    )

    if metric_by_row:
        results.update(_evaluate_predictions(
            preds=preds,
            targets=targets,
            num_tasks=num_tasks,
            metrics=metrics,
            dataset_type=dataset_type,
            metric_by_row=True,
            logger=logger
        ))

    return results


def evaluate(model: MoleculeModel,
             data_loader: MoleculeDataLoader,
             num_tasks: int,
             metrics: List[str],
             dataset_type: str,
             metric_by_row: bool = False,
             scaler: StandardScaler = None,
             logger: logging.Logger = None) -> Dict[str, List[float]]:
    """
    Evaluates an ensemble of models on a dataset by making predictions and then evaluating the predictions.

    :param model: A :class:`~chemprop.models.model.MoleculeModel`.
    :param data_loader: A :class:`~chemprop.data.data.MoleculeDataLoader`.
    :param num_tasks: Number of tasks.
    :param metrics: A list of names of metric functions.
    :param dataset_type: Dataset type.
    :param scaler: A :class:`~chemprop.features.scaler.StandardScaler` object fit on the training targets.
    :param metric_by_row: Whether to apply the metric to each row (and then average) rather than to each column.
    :param logger: A logger to record output.
    :return: A dictionary mapping each metric in :code:`metrics` to a list of values for each task.

    """
    preds = predict(
        model=model,
        data_loader=data_loader,
        scaler=scaler
    )

    results = evaluate_predictions(
        preds=preds,
        targets=data_loader.targets,
        num_tasks=num_tasks,
        metrics=metrics,
        dataset_type=dataset_type,
        metric_by_row=metric_by_row,
        logger=logger
    )

    return results
