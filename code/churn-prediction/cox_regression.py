import pickle
from churn_data import ChurnData, getChurnScores
import pandas as pd
import numpy as np
from lifelines import CoxPHFitter
from lifelines.utils import concordance_index
from sklearn.metrics import roc_auc_score, mean_squared_error
from sklearn.model_selection import StratifiedKFold

from multiprocessing import Pool
from functools import partial
import sys
sys.path.insert(0, '../utils')
from plot_format import *
# import seaborn as sns
from seaborn import apionly as sns

_RESULT_PATH = '../../results/churn/cox_regression/'

predPeriod = {
    'start': pd.Timestamp('2016-02-01'),
    'end': pd.Timestamp('2016-06-01')
}
predPeriodHours = (predPeriod['end'] - predPeriod['start']) / np.timedelta64(1, 'h')
# predPeriodHours = 2655

class CoxChurnModel:
    def __init__(self, data, penalizer=5000):
        self.cf = CoxPHFitter(penalizer=penalizer)
        self.data = data


    def fit(self, dataset, indices=None):
        if indices is not None:
            dataset = dataset.iloc[indices]

        self.cf.fit(dataset, 'deltaNextHours', event_col='observed')


    def predict_expectation(self, indices=None, dataset='train'):
        df = self.data.train_df

        if dataset=='test':
            df = self.data.test_df

        if indices is None:
            indices = self.data.split_val_ind

        x_df = df.iloc[indices]

        pred = self.cf.predict_expectation(x_df)

        return pred.values.reshape(-1)


    def predict_churn(self, pred_durations, indices=None, dataset='train'):
        df_unscaled = self.data.train_unscaled_df

        if dataset=='test':
            df_unscaled = self.data.test_unscaled_df

        if indices is None:
            indices = self.data.split_val_ind

        x_df_unscaled = df_unscaled.iloc[indices]

        churned = (pred_durations - x_df_unscaled.recency.values.reshape(-1)) > predPeriodHours

        return churned.reshape(-1)


    def getScores(self, indices=None, dataset='train'):
        df = self.data.train_df

        if dataset=='test':
            df = self.data.test_df

        if indices is None:
            indices = self.data.split_val_ind

        df = df.iloc[indices]

        pred_durations = self.predict_expectation(indices, dataset)
        pred_churn = self.predict_churn(pred_durations, indices, dataset)

        return {'churn': getChurnScores(~df.observed, pred_churn, pred_durations),
                'rmse_days': np.sqrt(mean_squared_error(df.deltaNextHours, pred_durations)) / 24,
                'concordance': concordance_index(df.deltaNextHours, pred_durations, df.observed)}


def runParameterSearch():
    """
    Cross-validated search for parameters

    """
    nFolds = 2
    nParams = 2
    nPools = 64

    # penalizer range
    penalizers = np.logspace(0,4,nParams)

    # load data
    data = ChurnData(predict='deltaNextHours')

    pool = Pool(nPools)

    cv = StratifiedKFold(n_splits=nFolds, shuffle=True, random_state=42)
    scores = [0] * nFolds

    for i, (train_ind, test_ind) in enumerate(cv.split(**data.train)):
        print('Fold: {} out of {}'.format(i+1, nFolds))
        scores[i] = pool.map(
                partial(_runParameterSearch, data=data, train_ind=train_ind, test_ind=test_ind),
                penalizers)

    pool.close()

    churn_scores = [[s['churn'] for s in ss] for ss in scores]
    churn_accuracy = np.array([[s['churn']['accuracy'] for s in ss] for ss in scores]).mean(0)
    churn_auc = np.array([[s['churn']['auc'] for s in ss] for ss in scores]).mean(0)
    rmse_days = np.array([[s['rmse_days'] for s in ss] for ss in scores]).mean(0)
    concordance = np.array([[s['concordance'] for s in ss] for ss in scores]).mean(0)

    res = {'penalizers': penalizers,
            'churn': {'scores': churn_scores, 'accuracy': churn_accuracy, 'auc': churn_auc},
            'rmse_days': rmse_days,
            'concordance': concordance}

    with open('{}penalizer_search.pkl'.format(_RESULT_PATH), 'wb') as handle:
        pickle.dump(res, handle, protocol=pickle.HIGHEST_PROTOCOL)

    return res


def _runParameterSearch(penalizer, data=None, train_ind=None, test_ind=None):
    model = CoxChurnModel(data, penalizer=penalizer)
    model.fit(data.train_df, indices=train_ind)

    return model.getScores(test_ind)


def storeModels():
    data = ChurnData(predict='deltaNextHours')
    model = CoxChurnModel(data)
    model.fit(data.split_train_df)

    with open(_RESULT_PATH+'model.pkl', 'wb') as handle:
        pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)

    pred_val = model.cf.predict_expectation(data.split_val_df).values.reshape(-1)

    with open(_RESULT_PATH+'pred_val.pkl', 'wb') as handle:
        pickle.dump(pred_val, handle, protocol=pickle.HIGHEST_PROTOCOL)


def showJointPlot(width=1, height=None):
    data = ChurnData(predict='deltaNextHours')#, features=['recency', 'logDeltaPrev_avg', 'logNumSessions'])
    observed = data.split_val_df.observed.values.astype('bool').reshape(-1)
    pred_val = pickle.load(open(_RESULT_PATH+'pred_val.pkl', 'rb'))

    df = pd.DataFrame()
    df['predicted'] = pred_val[observed]
    df['actual'] = data.split_val['y'][observed]

    jointgrid = sns.jointplot('actual', 'predicted', data=df, kind='kde', size=figsize(.5,.5)[0])

    plt.show()


# up to acc 0.712
def showChurnedPred(width=1, height=None):
    data = ChurnData(predict='deltaNextHours')#, features=['recency', 'logDeltaPrev_avg', 'logNumSessions'])
    observed = data.split_val_df.observed.values.astype('bool').reshape(-1)
    pred_val = pickle.load(open(_RESULT_PATH+'pred_val.pkl', 'rb'))

    returnDate = pred_val - data.split_val_unscaled_df.recency.values.reshape(-1)

    fig, ax = newfig(width, height)

    ax.hist(returnDate[observed], bins=50, label='not churned', alpha=.5)
    ax.hist(returnDate[~observed], bins=50, label='churned', alpha=.5)
    xDates = [pd.datetime(2016,i,1) for i in range(1,12)]
    xDatesHours = [(d - predPeriod['start']).to_timedelta64()/np.timedelta64(1,'h') for d in xDates]
    xDatesStr = [d.strftime('%Y-%m') for d in xDates]
    ax.set_xticks(xDatesHours)
    ax.set_xticklabels(xDatesStr)
    ax.axvline(x=predPeriodHours, label='prediction threshold' )
    ax.legend()

    fig.tight_layout()
    fig.show()



# data = ChurnData(predict='deltaNextHours')#, features=['recency', 'logDeltaPrev_avg', 'logNumSessions'])
# model = pickle.load(open(_RESULT_PATH+'model.pkl', 'rb'))
# pred_val = pickle.load(open(_RESULT_PATH+'pred_val.pkl', 'rb'))


