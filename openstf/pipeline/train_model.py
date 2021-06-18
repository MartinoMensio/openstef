# SPDX-FileCopyrightText: 2017-2021 Alliander N.V. <korte.termijn.prognoses@alliander.com> # noqa E501>
#
# SPDX-License-Identifier: MPL-2.0
import logging
from pathlib import Path
from typing import List, Union

import pandas as pd
from sklearn.base import RegressorMixin
import structlog

from openstf.feature_engineering.feature_applicator import TrainFeatureApplicator
from openstf.model.standard_deviation_generator import StandardDeviationGenerator
from openstf.model.model_creator import ModelCreator
from openstf.metrics.reporter import Reporter, Report
from openstf.model.serializer import PersistentStorageSerializer
from openstf.model_selection.model_selection import split_data_train_validation_test
from openstf.validation import validation

DEFAULT_TRAIN_HORIZONS: List[float] = [0.25, 24.0]
MAXIMUM_MODEL_AGE: int = 7

DEFAULT_EARLY_STOPPING_ROUNDS: int = 10
PENALTY_FACTOR_OLD_MODEL: float = 1.2


def train_model_pipeline(
    pj: dict,
    input_data: pd.DataFrame,
    check_old_model_age: bool,
    trained_models_folder: Union[str, Path],
    save_figures_folder: Union[str, Path],
) -> None:
    """Midle level pipeline that takes care of all persistent storage dependencies

    Args:
        pj (dict): Prediction job
        input_data (pd.DataFrame): Raw training input data
        check_old_model_age (bool): Check if training should be skipped because the model is too young
        trained_models_folder (Path): Path where trained models are stored
        save_figures_folder (Path): path were reports (mostly figures) about the training procces are stored

    Returns:
        None

    """
    # Intitialize logger and serializer
    logger = structlog.get_logger(__name__)
    serializer = PersistentStorageSerializer(trained_models_folder)

    # Get old model and age
    try:
        old_model = serializer.load_model(pid=pj["id"])
        old_model_age = (
            old_model.age
        )  # Age attribute is openstf specific and is added by the serializer
    except FileNotFoundError:
        old_model = None
        old_model_age = float("inf")
        logger.warning("No old model found, training new model")

    # Check old model age and continue yes/no
    if (old_model_age < MAXIMUM_MODEL_AGE) and check_old_model_age:
        logger.warning(
            f"Old model is younger than {MAXIMUM_MODEL_AGE} days, skip training"
        )
        return

    # Train model with core pipeline
    try:
        model, report = train_model_pipeline_core(pj, input_data, old_model)
    except RuntimeError as e:
        logger.error(f"Old model is better than new model for {pj['name']}", exc_info=e)
        return

    # Save model
    serializer.save_model(model, pid=pj["id"])

    # Save reports/figures
    report.save_figures(save_path=save_figures_folder)


def train_model_pipeline_core(
    pj: dict,
    input_data: pd.DataFrame,
    old_model: RegressorMixin = None,
    horizons: List[float] = None,
) -> tuple[RegressorMixin, Report]:
    """Train model core pipeline.
    Trains a new model given a predction job, input data and compares it to an old model.
    This pipeline has no database or persisitent storage dependencies.

    TODO once we have a data model for a prediction job this explantion is not
    required anymore.

    For training a model the following keys in the prediction job dictionairy are
    expected:
        "name"          Arbitray name only used for logging
        "model"         Model type, any of "xgb", "lgb",
        "hyper_params"  Hyper parameters dictionairy specific to the model_type
        "feature_names"      List of features to train model on or None to use all features

    Args:
        pj (dict): Prediction job
        input_data (pd.DataFrame): Input data
        old_model (RegressorMixin, optional): Old model to compare to. Defaults to None.
        horizons (List[float]): Horizons to train on in hours.

    Raises:
        ValueError: When input data is insufficient
        RuntimeError: When old model is better than new model

    Returns:
        tuple[RegressorMixin, Report]: Trained model and report (with figures)
    """

    if horizons is None:
        horizons = DEFAULT_TRAIN_HORIZONS

    logger = structlog.get_logger(__name__)
    # Validate and clean data
    validated_data = validation.clean(validation.validate(input_data))

    # Check if sufficient data is left after cleaning
    if not validation.is_data_sufficient(validated_data):
        raise ValueError(
            f"Input data is insufficient for {pj['name']} "
            f"after validation and cleaning"
        )

    # Add features
    data_with_features = TrainFeatureApplicator(
        horizons=horizons, feature_names=pj["feature_names"]
    ).add_features(validated_data)

    # Split data
    train_data, validation_data, test_data = split_data_train_validation_test(
        data_with_features.sort_index(axis=1)
    )

    # Create relevant model
    model = ModelCreator.create_model(pj)

    # split x and y data
    train_x, train_y = train_data.iloc[:, 1:], train_data.iloc[:, 0]
    validation_x, validation_y = validation_data.iloc[:, 1:], validation_data.iloc[:, 0]

    # Configure evals for early stopping
    eval_set = [(train_x, train_y), (validation_x, validation_y)]

    model.set_params(**pj["hyper_params"])
    model.fit(
        train_x,
        train_y,
        eval_set=eval_set,
        early_stopping_rounds=DEFAULT_EARLY_STOPPING_ROUNDS,
        verbose=False,
    )
    logging.info("Fitted a new model, not yet stored")

    # Check if new model is better than old model
    if old_model:
        combined = train_data.append(validation_data)
        x_data, y_data = combined.iloc[:, 1:], combined.iloc[:, 0]

        # Score method always returns R^2
        score_new_model = model.score(x_data, y_data)
        try:
            score_old_model = old_model.score(x_data, y_data)

            # Check if R^2 is better for old model
            if score_old_model > score_new_model * PENALTY_FACTOR_OLD_MODEL:
                raise (
                    RuntimeError(f"Old model is better than new model for {pj['name']}")
                )

            logger.info(
                "New model is better than old model, continuing with training procces"
            )
        except ValueError as e:
            logging.info(f"Could not compare to old model", exc_info=e)

    # Do confidence interval determination
    model = StandardDeviationGenerator(
        pj, validation_data
    ).generate_standard_deviation_data(model)

    # Report about the training procces
    report = Reporter(pj, train_data, validation_data, test_data).generate_report(model)

    return model, report
