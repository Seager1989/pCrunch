from __future__ import print_function
import os, sys, time, shutil
from functools import partial
import multiprocessing as mp
import numpy as np
import ruamel_yaml as ry
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from wisdem.aeroelasticse.Util.ReadFASTout import ReadFASToutFormat
from wisdem.aeroelasticse.Util.FileTools   import get_dlc_label, load_case_matrix, load_file_list, save_yaml
from wisdem.aeroelasticse.Util.spectral    import fft_wrap

from ROSCO_toolbox.utilities import FAST_IO

from BatchAnalysis import pdTools

class Loads_Analysis(object):
    '''
    Contains analysis tools to post-process OpenFAST output data. Most methods are written to support
    single instances of output data to ease parallelization. 

    Methods:
    --------
    full_loads_analysis
    summary_stats
    load_ranking
    fatigue
    '''
    def __init__(self, **kwargs):

        # Analysis time range
        self.t0 = 0
        self.tf = 1000000
        # Desired channels for analysis
        self.channel_list = []

        # verbose?
        self.verbose=False

        for k, w in kwargs.items():
            try:
                setattr(self, k, w)
            except:
                pass

        super(Loads_Analysis, self).__init__()

    def full_loads_analysis(self, filenames, get_load_ranking=True, return_FastData=False):
        '''
        Load openfast data - get statistics - get load ranking - return data
        NOTE: Can be called to run in parallel if get_load_ranking=False (see Processing.batch_processing)

        Inputs:
        -------
        filenames: list
            List of filenames to load and analyse
        get_load_ranking: bool, optional
            Get the load ranking for all cases
        return_FastData, bool
            Return a dictionary or list constaining OpenFAST output data

        Outputs:
        --------
        sum_stats: dict
            dictionary of summary statistics
        load_rankings: dict
            dictionary of load rankings
        fast_data: list or dict
            list or dictionary containing OpenFAST output data
        '''
        # Load openfast data
        fast_io = FAST_IO()
        fast_data = fast_io.load_FAST_out(filenames, tmin=self.t0, tmax=self.tf, verbose=self.verbose)

        # Get summary statistics
        sum_stats = self.summary_stats(fast_data, verbose=self.verbose)

        # Get load rankings
        if get_load_ranking:
            load_rankings = self.load_ranking(sum_stats)


        if return_FastData:
            return sum_stats, fast_data
        if get_load_ranking: 
            return sum_stats, load_rankings
        if return_FastData and get_load_ranking:
            return sum_stats, load_rankings, fast_data
        else:
            return sum_stats

    def summary_stats(self, fast_data, channel_list=[], verbose=False):
        '''
        Get summary statistics from openfast output data. 

        Parameters
        ----------
        fast_data: list
            List of dictionaries containing openfast output data (returned from ROSCO_toolbox.FAST_IO.load_output)
        channel_list: list
            list of channels to collect data from. Defaults to all
        verbose: bool, optional
            Print some status info

        Returns
        -------
        data_out: dict
            Dictionary containing summary statistics
        fast_outdata: dict, optional
            Dictionary of all OpenFAST output data. Only returned if return_data=true
        '''
        sum_stats = {}
        for fd in fast_data:
            if verbose:
                print('Processing data for {}'.format(fd['meta']['name']))

            # Build channel list if it isn't input
            if channel_list == []:
                channel_list = fd.keys()

            # Process Data
            for channel in channel_list:
                if channel != 'Time' and channel != 'meta' and channel in channel_list:
                    try:
                        if channel not in sum_stats.keys():
                            sum_stats[channel] = {}
                            sum_stats[channel]['min'] = []
                            sum_stats[channel]['max'] = []
                            sum_stats[channel]['std'] = []
                            sum_stats[channel]['mean'] = []
                            sum_stats[channel]['abs'] = []
                            sum_stats[channel]['integrated'] = []

                        sum_stats[channel]['min'].append(float(min(fd[channel])))
                        sum_stats[channel]['max'].append(float(max(fd[channel])))
                        sum_stats[channel]['std'].append(float(np.std(fd[channel])))
                        sum_stats[channel]['mean'].append(float(np.mean(fd[channel])))
                        sum_stats[channel]['abs'].append(float(max(np.abs(fd[channel]))))
                        sum_stats[channel]['integrated'].append(
                            float(np.trapz(fd['Time'], fd[channel])))

                    except ValueError:
                        print('Error loading data from {}.'.format(channel))
                    except:
                        print('{} is not in available OpenFAST output data'.format(channel))
            

        return sum_stats


    def load_ranking(self, stats, ranking_stats, ranking_vars, names=[], get_df=False):
        '''
        Find load rankings for desired signals

        Inputs:
        -------
        stats: dict, list, pd.DataFrame
            summary statistic information
        ranking_stats: list
            desired statistics to rank for load ranking (e.g. ['max', 'std'])
        ranking_vars: list
            desired variables to for load ranking (e.g. ['GenTq', ['RootMyb1', 'RootMyb2', 'RootMyb3']]) 
        names: list of strings, optional
            names corresponding to each dataset
        get_df: bool, optional
            Return pd.DataFrame of data?
        
        Returns:
        -------
        load_ranking: dict
            dictionary containing load rankings
        load_ranking_df: pd.DataFrame
            pandas DataFrame containing load rankings
        '''
        
        # Make sure stats is in pandas df
        if isinstance(stats, dict):
            stats_df = pdTools.dict2df([stats], names=names)
        elif isinstance(stats, list):
            stats_df = pdTools.dict2df(stats, names=names)
        elif not isinstance(stats, pd.DataFrame):
            raise TypeError('Input stats is must be a dictionary, list, or pd.DataFrame containing OpenFAST output statistics.')


        # Ensure naming consitency
        if not names:
            names = list(stats_df.columns.levels[0])

        # Column names to search in stats_df
        #  - [name, variable, stat],  i.e.['DLC1.1','TwrBsFxt','max']
        cnames = [pd.MultiIndex.from_product([names, var, [stat]])
                for var, stat in zip(ranking_vars, ranking_stats)]

        # Collect load rankings
        collected_rankings = []
        for col in cnames:
            # Set column names for dataframe
            mi_name = list(col.levels[0])
            mi_stat = col.levels[2]  # length = 1
            mi_idx = col.levels[2][0] + '_case_idx'
            if len(col.levels[1]) > 0:
                mi_var = [col.levels[1][0][:-1]]
            else:
                mi_var = list(col.levels[1])
            mi_colnames = pd.MultiIndex.from_product([mi_name, mi_var, [mi_idx, mi_stat[0]]])

            # Check for valid stats
            for c in col:
                if c not in list(stats_df.columns.values):
                    print('WARNING: {} does not exist in statistics.'.format(c))
                    col = col.drop(c)
                    # raise ValueError('{} does not exist in statistics'.format(c))
            # Go to next case if no [stat, var] exists in this set
            if len(col) == 0:
                continue
            # Extract desired variables from stats dataframe
            if mi_stat in ['max', 'abs']:
                var_df = stats_df[col].max(axis=1, level=0)
            elif mi_stat in ['min']:
                var_df = stats_df[col].min(axis=1, level=0)
            elif mi_stat in ['mean', 'std']:
                var_df = stats_df[col].mean(axis=1, level=0)

            # Combine ranking dataframes
            var_df_list = [var_df[column].sort_values(
                ascending=False).reset_index() for column in var_df.columns]
            single_lr = pd.concat(var_df_list, axis=1)
            single_lr.columns = mi_colnames
            collected_rankings.append(single_lr)

        # Combine dataframes for each case
        load_ranking_df = pd.concat(collected_rankings, axis=1).sort_index(axis=1)
        # Generate dict of info
        load_ranking = pdTools.df2dict(load_ranking_df)

        if get_df:
            return load_ranking, load_ranking_df
        else:
            return load_ranking

    def fatigue(self):
        '''
        Fatigue loads analysis
        '''
        pass

class Power_Production(object):
    '''
    Class to generate power production stastics
    '''
    def __init__(self, **kwargs):
        # Wind speeds to analyse power production over
        self.windspeeds=[]

        for k, w in kwargs.items():
            try:
                setattr(self, k, w)
            except:
                pass

        super(Power_Production, self).__init__()

    def gen_windPDF(self, Vavg, bnums, Vrange):
        ''' 
        Generates a probability vector by finding the difference between bin edges using a Rayleigh
        wind distribution with shape factor = 2. Note this method differs slightly from IEC standard, but results end
        up being very close especially with higher resolution
        
        Inputs:
        -------
        Vavg: float
            average wind speed of the site 
        bnums: int
            number of bins within brange
        Vrange: list
            range of wind speeds being considered, ie. [2,26]
        
        Outputs:
        ----------
        p_bin: list
            list containing probabilities per wind speed bin 
        '''
        # Check for windspeeds
        if not len(self.windspeeds):
            raise ValueError('Power_Production.windspeeds must be defined for any power production analysis!')

        _, edges = np.histogram(self.windspeeds, bins=bnums, range=Vrange)
        #centers = edges[:-1] + np.diff(edges) / 2.
        self.p_bin = []
        for x in range(1, len(edges)):
            self.p_bin.append(np.exp(-np.pi*(edges[x-1]/(2*Vavg)) **
                                2)-np.exp(-np.pi*(edges[x]/(2*Vavg))**2))
        
        return self.p_bin

    def AEP(self, stats):
        '''
        Get AEPs for simulation cases

        TODO: Print/Save this someplace besides the console
    
        Inputs:
        -------
        stats: dict, list, pd.DataFrame
            Dict (single case), list(multiple cases), df(single or multiple cases) containing
            summary statistics. 

        Returns:
        --------
        AEP: List
            Annual energy production corresponding to 
        '''
        # Check for wind speeds
        if not isinstance(self.p_bin, list):
            raise ValueError('Wind speed probabilities do not exist, run gen_WindPDF before AEP.')
        
        # Make sure stats is in pandas df
        if isinstance(stats, dict):
            stats_df = pdTools.dict2df(stats)
        elif isinstance(stats, list):
            stats_df = pdTools.dict2df(stats)
        elif not isinstance(stats, pd.DataFrame):
            raise TypeError('Input stats is must be a dictionary, list, or pd.DataFrame containing OpenFAST output statistics.')

        if 'GenPwr' in stats_df.columns.levels[0]:
            pwr_array = np.array(stats_df.loc[:, ('GenPwr', 'mean')])
            AEP = np.matmul(pwr_array.T, self.p_bin) * 8760
        elif 'GenPwr' in stats_df.columns.levels[1]:
            pwr_array = np.array(stats_df.loc[:, (slice(None), 'GenPwr', 'mean')])
            AEP = np.matmul(pwr_array.T, self.p_bin) * 8760
        else:
            raise ValueError("('GenPwr','Mean') does not exist in the input statistics.")

        return AEP

class wsPlotting(object):
    '''
    General plotting scripts.
    '''

    def __init__(self):
        pass

    def stat_curve(self, windspeeds, stats, plotvar, plottype, stat_idx=0, names=[]):
        '''
        Plot the turbulent power curve for a set of data. 
        Can be plotted as bar (good for comparing multiple cases) or line 

        Inputs:
        -------
        windspeeds: list-like
            List of wind speeds to plot
        stats: list, dict, or pd.DataFrame
            Dict (single case), list(multiple cases), df(single or multiple cases) containing
            summary statistics. 
        plotvar: str
            Type of variable to plot
        plottype: str
            bar or line 
        stat_idx: int, optional
            Index of datasets in stats to plot from
        
        Returns:
        --------
        fig: figure handle
        ax: axes handle
        '''

        # Check for valid inputs
        if isinstance(stats, dict):
            stats_df = pdTools.dict2df(stats)
            if any((stat_inds > 0) or (isinstance(stat_inds, list))):
                print('WARNING: stat_ind = {} is invalid for a single stats dictionary. Defaulting to stat_inds=0.')
                stat_inds = 0
        elif isinstance(stats, list):
            stats_df = pdTools.dict2df(stats)
        elif isinstance(stats, pd.DataFrame):
            stats_df = stats
        else:
            raise TypeError(
                'Input stats must be a dictionary, list, or pd.DataFrame containing OpenFAST output statistics.')

       
        # Check windspeed length
        if len(windspeeds) == len(stats_df):
            ws = windspeeds
        elif int(len(windspeeds)/len(stats_df.columns.levels[0])) == len(stats_df):
            ws = windspeeds[0:len(stats_df)]
        else:
            raise ValueError('Length of windspeeds is not the correct length for the input statistics')

        # Get statistical data for desired plot variable
        if plotvar in stats_df.columns.levels[0]:
            sdf = stats_df.loc[:, (plotvar, slice(None))].droplevel([0], axis=1)
        elif plotvar in stats_df.columns.levels[1]:
            sdf = stats_df.loc[:, (slice(None), plotvar, slice(None))].droplevel([1], axis=1)
        else:
            raise ValueError("('GenPwr','Mean') does not exist in the input statistics.")
        
        # Add windspeeds to data
        sdf['WindSpeeds']= ws
        # Group by windspeed and average each statistic (for multiple seeds)
        sdf = sdf.groupby('WindSpeeds').mean() 
        # Final wind speed values
        pl_windspeeds=sdf.index.values

        if plottype == 'bar':
            # Define mean and std dataframes
            means = sdf.loc[:, (slice(None), 'mean')].droplevel(1, axis=1)
            std = sdf.loc[:, (slice(None), 'std')].droplevel(1, axis=1)
            # Plot bar charts
            fig, ax = plt.subplots()
            means.plot.bar(yerr=std, ax=ax, title=plotvar, capsize=2)
            ax.legend(names,loc='upper left')

        if plottype == 'line':
            # Define mean, min, max, and std dataframes
            means = sdf.loc[:, (sdf.columns.levels[0][stat_idx], 'mean')]
            smax = sdf.loc[:, (sdf.columns.levels[0][stat_idx], 'max')]
            smin = sdf.loc[:, (sdf.columns.levels[0][stat_idx], 'min')]
            std = sdf.loc[:, (sdf.columns.levels[0][stat_idx], 'std')]

            fig, ax = plt.subplots()
            ax.errorbar(pl_windspeeds, means, [means - smin, smax - means],
                         fmt='k', ecolor='gray', lw=1, capsize=2)
            means.plot(yerr=std, ax=ax, 
                        capsize=2, lw=3, 
                        elinewidth=2, 
                        title=names[0] + ' - ' + plotvar)
            plt.grid(lw=0.5, linestyle='--')

        return fig, ax


    def distribution(self, fast_data, channels, caseid, names=None, kde=True):
        '''
        Distributions of data from desired fast runs and channels

        Parameters
        ----------
        fast_data: dict, list
            List or Dictionary containing OpenFAST output data from desired cases to compare
        channels: list
            List of strings of OpenFAST output channels e.g. ['RotSpeed','GenTq']
        caseid: list
            List of caseid's to compare
        names: list, optional
            Names of the runs to compare
        fignum: ind, (optional)
            Specified figure number. Useful to plot over previously made plot
        
        Returns:
        --------
        fig: figure handle
        ax: axes handle
        '''
        # Make sure input types allign
        if isinstance(fast_data, dict):
            fd = [fast_data]
        elif isinstance(fast_data, list):
            if len(caseid) == 1:
                fd = [fast_data[caseid[0]]]
            else:
                fd = [fast_data[case] for case in caseid]
        else:
            raise ValueError('fast_data is an improper data type')
            

        # if not names:
        #     names = [[]]*len(fd)

        for channel in channels:
            fig, ax = plt.subplots()
            for idx, data in enumerate(fd):
                # sns.kdeplot(data[channel], shade=True, label='case '+ str(idx))
                sns.distplot(data[channel], kde=kde, label='case ' + str(idx))  # For a histogram
                ax.set_title(channel + ' distribution')
            if names:
                ax.legend(names)
                
        return fig, ax