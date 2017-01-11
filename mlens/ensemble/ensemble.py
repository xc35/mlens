#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
author: Sebastian Flennerhag
date: 11/01/2017
Stacked ensemble class for full control over the entire model's parameters.
Scikit-learn API allows full integration, including grid search and pipelining.
"""

from sklearn.base import clone, BaseEstimator, TransformerMixin, RegressorMixin
from ._setup import name_estimators, name_base, _check_names
from ._clone import _clone_base_estimators, _clone_preprocess_cases
from ..utils import print_time
from ..parallel import preprocess_folds, preprocess_pipes
from ..parallel import fit_estimators, folded_predictions
from sklearn.externals import six
from time import time


class Ensemble(BaseEstimator, RegressorMixin, TransformerMixin):
    '''
    Meta estimator class that blends a set of base estimators via a meta
    estimator. In difference to standard stacking, where the base estimators
    predict the same data they were fitted on, this class uses k-fold splits of
    the the training data make base estimators predict out-of-sample training
    data. Since base estimators predict training data as in-sample, and test
    data as out-of-sample, standard stacking suffers from a bias in that the
    meta estimators fits based on base estimator training error, but predicts
    based on base estimator test error. This blends overcomes this by splitting
    up the training set in the fitting stage, to create near identical for both
    training and test set. Thus, as the number of folds is increased, the
    training set grows closer in remeblance of the test set, but at the cost of
    increased fitting time.

    Parameters
    -----------
    meta_estimator : obj
        estimator to fit on base_estimator predictions. Must accept fit and
        predict method.
    base_pipelines : dict, list
        base estimator pipelines. If no preprocessing, pass a list of
        estimators, possible as named tuples [('est-1', est), (...)]. If
        preprocessing is desired, pass a dictionary with pipeline keys:
        {'pipe-1': [preprocessing], [estimators]}, where
        [preprocessing] should be a list of transformers, possible as named
        tuples, and estimators should be a list of estimators to fit on
        preprocesssed data, possibly as named tuples. General format should be
        {'pipe-1', [('step-1', trans), (...)], [('est-1', est), (...)]}, where
        named each step is optional. Each transformation step and estimators
        must accept fit and transform / predict methods respectively
    folds : int, default=10
        number of folds to use for constructing meta estimator training set
    shuffle : bool, default=True
        whether to shuffle data for creating k-fold out of sample predictions
    as_df : bool, default=False
        whether to fit meta_estimator on a dataframe. Useful if meta estimator
        allows feature importance analysis
    verbose : bool, int, default=False
        level of verbosity of fitting
    n_jobs : int, default=10
        number of CPU cores to use for fitting and prediction
    '''

    def __init__(self, meta_estimator, base_pipelines, folds=10,
                 shuffle=True, as_df=False, verbose=False, n_jobs=-1):

        self.base_pipelines = base_pipelines
        self.meta_estimator = meta_estimator

        self.named_meta_estimator = name_estimators([meta_estimator], 'meta-')
        self.named_base_pipelines = name_base(base_pipelines)

        # if preprocessing, seperate pipelines
        if isinstance(base_pipelines, dict):
            self.preprocess = [(case, _check_names(p[0])) for case, p in
                               base_pipelines.items()]
            self.base_estimators = [(case, _check_names(p[1])) for case, p in
                                    base_pipelines.items()]
        else:
            self.preprocess = []
            self.base_estimators = [(case, p) for case, p in base_pipelines]

        self.folds = folds
        self.shuffle = self.shuffle
        self.as_df = as_df
        self.verbose = verbose
        self.n_jobs = n_jobs

    def fit(self, X, y):
        '''
        Parameters
        ----------
        X : array-like, shape=[n_samples, n_features]
            input matrix to be used for prediction
        y : array-like, shape=[n_samples, ]
            output vector to trained estimators on
        Returns
        --------
        self : obj
            class instance with fitted estimators
        '''
        self.meta_estimator_ = clone(self.meta_estimator)
        self.base_estimators_ = _clone_base_estimators(self.base_estimators)
        self.preprocess_ = _clone_preprocess_cases(self.preprocess)

        if self.verbose > 0:
            print('Fitting ensemble')
            ts = time()

        # ========== Fit meta estimator ==========
        # Fit temporary base pipelines and make k-fold out of sample preds

        # Parellelized preprocessing for all folds
        data = preprocess_folds(_clone_preprocess_cases(self.preprocess),
                                X, y, self.folds, self.shuffle, True,
                                self.n_jobs, self.verbose)

        # Parellelized k-fold predictions for meta estiamtor training set
        M = folded_predictions(data,
                               _clone_base_estimators(self.base_estimators),
                               X.shape[0], self.as_df, self.n_jobs,
                               self.verbose)

        self.meta_estimator_.fit(M, y)

        # ========== Fit preprocessing and base estimator ==========

        # Parallelized fitting of preprocessing pipelines
        out = preprocess_pipes(self.preprocess_, X, y, return_estimators=True,
                               n_jobs=self.n_jobs, verbose=self.verbose)
        pipes, Z, cases = zip(*out)

        self.preprocess_ = [(case, pipe) for case, pipe in zip(cases, pipes)]

        # Parallelized fitting of base estimators (on full training data)
        data = [[z, case] for z, case in zip(Z, cases)]
        self.base_estimators_ = fit_estimators(data, y, self.base_estimators_,
                                               self.n_jobs, self.verbose)

        if self.verbose > 0:
            print_time(ts, 'Fit complete')

        return self

    def predict(self, X, y=None):
        '''
        Parameters
        ----------
        X : array-like, shape=[n_samples, n_features]
            input matrix to be used for prediction
        Returns
        --------
        y : array-like, shape=[n_samples, ]
            predictions for provided input array
        '''
        if hasattr(self, 'base_estimators_'):
            M = self.base_estimators_.predict(X)
        else:
            M = None

        if hasattr(self, 'base_feature_pipelines_'):
            M = self._predict_pipeline(M, X, fitted=True)

        return self.meta_estimator_.predict(M)

    def get_params(self, deep=True):
        ''' Sklearn API for retrieveing all (also nested) model parameters'''
        if not deep:
            return super(Ensemble, self).get_params(deep=False)
        else:
            out = self.__dict__

            out.update(self.named_base_estimators.copy())
            for name, step in six.iteritems(self.named_base_estimators):
                for key, value in six.iteritems(step.get_params(deep=True)):
                    out['%s__%s' % (name, key)] = value

            out.update(self.named_base_feature_pipelines.copy())
            for name, step in six.iteritems(self.named_base_feature_pipelines):
                for key, value in six.iteritems(step.get_params(deep=True)):
                    out['%s__%s' % (name, key)] = value

            out.update(self.named_meta_estimator.copy())
            for name, step in six.iteritems(self.named_meta_estimator):
                for key, value in six.iteritems(step.get_params(deep=True)):
                    out['%s__%s' % (name, key)] = value
            return out

class PredictionFeature(object):
    """ TBD """
    pass