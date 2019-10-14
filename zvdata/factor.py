# -*- coding: utf-8 -*-
import enum
from typing import List, Union

import pandas as pd

from zvdata import IntervalLevel
from zvdata.api import get_data, df_to_db
from zvdata.chart import Drawer
from zvdata.normal_data import NormalData
from zvdata.reader import DataReader, DataListener
from zvdata.scorer import Transformer, Scorer
from zvdata.sedes import Jsonable
from zvdata.utils.pd_utils import df_is_not_null


class FactorType(enum.Enum):
    filter = 'filter'
    score = 'score'
    state = 'state'


# factor class registry
factor_cls_registry = {}

# factor instance registry
factor_instance_registry = {}


def register_instance(cls, instance):
    if cls.__name__ not in ('Factor', 'FilterFactor', 'ScoreFactor', 'StateFactor'):
        factor_instance_registry[cls.__name__] = instance


def register_class(target_class):
    if target_class.__name__ not in ('Factor', 'FilterFactor', 'ScoreFactor', 'StateFactor'):
        factor_cls_registry[target_class.__name__] = target_class


class Meta(type):
    def __new__(meta, name, bases, class_dict):
        cls = type.__new__(meta, name, bases, class_dict)
        register_class(cls)
        return cls


class Factor(DataReader, DataListener, Jsonable):
    factor_type: FactorType = None

    factor_schema = None

    def __init__(self,
                 data_schema: object,
                 entity_ids: List[str] = None,
                 entity_type: str = 'stock',
                 exchanges: List[str] = ['sh', 'sz'],
                 codes: List[str] = None,
                 the_timestamp: Union[str, pd.Timestamp] = None,
                 start_timestamp: Union[str, pd.Timestamp] = None,
                 end_timestamp: Union[str, pd.Timestamp] = None,
                 columns: List = None,
                 filters: List = None,
                 order: object = None,
                 limit: int = None,
                 provider: str = 'eastmoney',
                 level: Union[str, IntervalLevel] = IntervalLevel.LEVEL_1DAY,
                 category_field: str = 'entity_id',
                 time_field: str = 'timestamp',
                 auto_load: bool = True,
                 valid_window: int = 250,
                 # child added arguments
                 keep_all_timestamp: bool = False,
                 fill_method: str = 'ffill',
                 effective_number: int = 10,
                 transformers: List[Transformer] = [],
                 need_persist: bool = True) -> None:
        self.need_persist = need_persist
        if need_persist:
            self.pipe_df = get_data(provider='zvt',
                                    data_schema=self.factor_schema,
                                    start_timestamp=start_timestamp,
                                    index=[category_field, time_field])

            if df_is_not_null(self.pipe_df):
                self.logger.info('input start_timestamp:{}'.format(start_timestamp))
                start_timestamp = self.pipe_df.iloc[-1]['timestamp']
                self.logger.info('factor start_timestamp:{}'.format(start_timestamp))

        else:
            self.pipe_df: pd.DataFrame = None

        self.result_df: pd.DataFrame = None

        super().__init__(data_schema, entity_ids, entity_type, exchanges, codes, the_timestamp, start_timestamp,
                         end_timestamp, columns, filters, order, limit, provider, level,
                         category_field, time_field, auto_load, valid_window)

        self.factor_name = type(self).__name__.lower()

        self.keep_all_timestamp = keep_all_timestamp
        self.fill_method = fill_method
        self.effective_number = effective_number
        self.transformers = transformers

        self.register_data_listener(self)

    def pre_compute(self):
        self.pipe_df = self.data_df

    def do_compute(self):
        if df_is_not_null(self.pipe_df) and self.transformers:
            for transformer in self.transformers:
                self.pipe_df = transformer.transform(self.pipe_df)

        if self.need_persist:
            self.persist_result()

    def after_compute(self):
        self.fill_gap()
        self.persist_result()

    def compute(self):
        """
        implement this to calculate factors normalize to [0,1]

        """
        self.pre_compute()
        self.do_compute()
        self.after_compute()

    def __repr__(self) -> str:
        return self.result_df.__repr__()

    def get_result_df(self):
        return self.result_df

    def get_pipe_df(self):
        return self.pipe_df

    def pipe_drawer(self) -> Drawer:
        drawer = Drawer(NormalData(df=self.pipe_df))
        return drawer

    def result_drawer(self) -> Drawer:
        return Drawer(NormalData(df=self.result_df))

    def draw_pipe(self, chart='line', plotly_layout=None, annotation_df=None, render='html', file_name=None,
                  width=None, height=None,
                  title=None, keep_ui_state=True, **kwargs):
        return self.pipe_drawer().draw(chart=chart, plotly_layout=plotly_layout, annotation_df=annotation_df,
                                       render=render, file_name=file_name,
                                       width=width, height=height, title=title, keep_ui_state=keep_ui_state, **kwargs)

    def draw_result(self, chart='line', plotly_layout=None, annotation_df=None, render='html', file_name=None,
                    width=None, height=None,
                    title=None, keep_ui_state=True, **kwargs):
        return self.result_drawer().draw(chart=chart, plotly_layout=plotly_layout, annotation_df=annotation_df,
                                         render=render, file_name=file_name,
                                         width=width, height=height, title=title, keep_ui_state=keep_ui_state, **kwargs)

    def fill_gap(self):
        if self.keep_all_timestamp:
            idx = pd.date_range(self.start_timestamp, self.end_timestamp)
            new_index = pd.MultiIndex.from_product([self.result_df.index.levels[0], idx],
                                                   names=['entity_id', self.time_field])
            self.result_df = self.result_df.loc[~self.result_df.index.duplicated(keep='first')]
            self.result_df = self.result_df.reindex(new_index)
            self.result_df = self.result_df.fillna(method=self.fill_method, limit=self.effective_number)

    def on_data_loaded(self, data: pd.DataFrame):
        # the pipe_df has been loaded from db
        if self.need_persist and df_is_not_null(self.pipe_df):
            return
        self.compute()

    def on_data_changed(self, data: pd.DataFrame):
        """
        overwrite it for computing after data added

        Parameters
        ----------
        data :
        """
        self.compute()

    def on_entity_data_changed(self, entity, added_data: pd.DataFrame):
        """
        overwrite it for computing after entity data added

        Parameters
        ----------
        entity :
        added_data :
        """
        pass

    def persist_result(self):
        df_to_db(df=self.pipe_df, data_schema=self.factor_schema, provider='zvt')

    def get_latest_saved_pipe(self):
        order = eval('self.factor_schema.{}.desc()'.format(self.time_field))

        records = get_data(provider=self.provider,
                           data_schema=self.pipe_schema,
                           order=order,
                           limit=1,
                           return_type='domain',
                           session=self.session)
        if records:
            return records[0]
        return None


class FilterFactor(Factor):
    factor_type = FactorType.filter


class ScoreFactor(Factor):
    factor_type = FactorType.score

    def __init__(self, data_schema: object, entity_ids: List[str] = None, entity_type: str = 'stock',
                 exchanges: List[str] = ['sh', 'sz'], codes: List[str] = None,
                 the_timestamp: Union[str, pd.Timestamp] = None, start_timestamp: Union[str, pd.Timestamp] = None,
                 end_timestamp: Union[str, pd.Timestamp] = None, columns: List = None, filters: List = None,
                 order: object = None, limit: int = None, provider: str = 'eastmoney',
                 level: Union[str, IntervalLevel] = IntervalLevel.LEVEL_1DAY, category_field: str = 'entity_id',
                 time_field: str = 'timestamp', auto_load: bool = True, keep_all_timestamp: bool = False,
                 fill_method: str = 'ffill', effective_number: int = 10, transformers: List[Transformer] = [],
                 scorer: Scorer = None) -> None:
        self.scorer = scorer
        super().__init__(data_schema, entity_ids, entity_type, exchanges, codes, the_timestamp, start_timestamp,
                         end_timestamp, columns, filters, order, limit, provider, level, category_field, time_field,
                         auto_load, keep_all_timestamp, fill_method, effective_number, transformers)

    def do_compute(self):
        super().do_compute()

        if df_is_not_null(self.pipe_df) and self.scorer:
            self.result_df = self.scorer.score(self.data_df)


class StateFactor(Factor):
    factor_type = FactorType.state
    states = []

    def get_state(self, timestamp, entity_id):
        pass

    def get_short_state(self):
        pass

    def get_long_state(self):
        pass
