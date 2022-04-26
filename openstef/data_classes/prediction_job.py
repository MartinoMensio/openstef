# SPDX-FileCopyrightText: 2017-2022 Contributors to the OpenSTEF project <korte.termijn.prognoses@alliander.com> # noqa E501>
#
# SPDX-License-Identifier: MPL-2.0

from typing import List, Optional, Union

from pydantic import BaseModel

from openstef.data_classes.model_specifications import ModelSpecificationDataClass


class PredictionJobDataClass(BaseModel):
    id: Union[int, str]
    model: str
    forecast_type: str
    horizon_minutes: int
    resolution_minutes: int
    lat: float
    lon: float
    name: str
    train_components: Optional[bool]
    description: Optional[str]
    quantiles: Optional[List[float]]
    train_horizons_minutes: Optional[List[int]]
    default_modelspecs: Optional[ModelSpecificationDataClass]
    save_train_forecasts: bool = False
    completeness_treshold: float = 0.5
    minimal_table_length: int = 100
    flatliner_treshold: int = 24
    depends_on: Optional[List[Union[int, str]]]

    def __getitem__(self, item):
        """Allows us to use subscription to get the items from the object"""
        return getattr(self, item)

    def __setitem__(self, key: str, value: any):
        """Allows us to use subscription to set the items in the object"""
        if hasattr(self, key):
            self.__dict__[key] = value
        else:
            raise AttributeError(f"{key} not an attribute of prediction job.")

    # The following configuration is needed to prevent ids in "depends_on"
    # to be converted from int to str when we use integer ids
    class Config:
        smart_union = True
