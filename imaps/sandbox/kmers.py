"""Analysis of kmers located around locations of interest.

First step is regional thresholding to obtain thresholded crosslinks (txn).
This approach takes crosslinks in all peaks within a region to define
threshold and so introduces an element of intra-regional comparison.
Regions for thresholding as defined in the following way:
- all exons in the same gene (5'UTR, CDS, 3'UTR, or all exons in ncRNAs)
  are considered one region,
- each intron is its own region,
- each intergenic region is its own region.
Next step is kmer analysis. For this step regions are defined slightly
different:
- whole genome,
- introns,
- 3'UTR eksons,
- 5'UTR eksons,
- all other coding exon regions,
- ncRNA (all other genes),
- intergenic,
- whole gene
For whole gene and other exons
Proceed only with those regions where tXn>100. For all analyses, exclude
chrM and those scaffolds not included in the genome annotations.
For each kmer, first count occurences at each specific position relative to
thresholded crosslinks (Otxn). Center of kmers is used to report kmers position
(for even kmers position before the center is used).
Next positions of the maximum count for each kmer in region -15 to 15 are found
(mtxn). From Otxn we subset distal regions, -150 to 100 and 100 to 150 and
calculate average counts which are called distal occurences Dtxn.
We proceed then to calculate rtxn and roxn which are relative occurences of each
kmer at each position around txn and oxn respectivly calculated as Otxn / Dtxn
and Ooxn / Dtxn. Term oxn is used for reference crosslinks, defined as those not
in peaks.
All positions within -60 to 60 around txn where rtxn > 1.5 are called prtxn and
are used in next step where we calculate average rtxn across prtxn positions
relative to txn and average roxn across prtxn positions relative to oxn. These
averages are called artxn and aroxn.
Enrichment around thresholded crosslinks etxn is calculated as log2(artxn/aroxn)
and reported in the outfile table.
For z-score calculation proceedure is similar to the one described above with
the exception that aroxn is calculated from 30 random samples of oxn in order
to obtain mean aroxn and its standard deviation for each kmer using formula:
z-score = (artxn - mean(aroxn)) / std(aroxn)
From z-score p-values are obtained and reported in the outfile table.
So obtained z-scores are used to rank kmers and top kmers are chosen for
plotting. Number of top kmers to be plotted and number of clusters are user
defined.
The k-means clustering is used to define groups of kmers that have most
similar enrichment distribution, to be shown on each plot. Plots are
ordered by the max enrichment value of the most enriched kmer in the
cluster. To name the clusters an attempt is made to find a consensus
sequence whenever possible or if not the most enriched motif is
returned.
Finally a last plot showing positional enrichment percentage averaged
for each cluster over a larger window is drawn. All the figures and several
tables are saved and available for inspection.
"""

import os
from itertools import product, combinations
from collections import OrderedDict
import csv
import random
from random import randint
import shutil
import gzip
import copy
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pybedtools as pbt
import seaborn as sns
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from plumbum import local
from plumbum.cmd import zcat, sort
import scipy

REGIONS = [
    'whole_gene',
    'intron',
    'UTR3',
    'other_exon',
    'UTR5',
    'ncRNA',
    'intergenic',
    'genome'
]
REGION_SITES = {
    'genome': ['intron', 'CDS', 'UTR3', 'UTR5', 'ncRNA', 'intergenic'],
    'whole_gene': ['intron', 'CDS', 'UTR3', 'UTR5'],
    'intergenic': ['intergenic'],
    'intron': ['intron'],
    'ncRNA': ['ncRNA'],
    'other_exon': ['UTR5', 'CDS'],
    'UTR3': ['UTR3'],
    'UTR5': ['UTR5']
}
REGIONS_QUANTILE = ['intron', 'intergenic', 'cds_utr_ncrna']
REGIONS_MAP = {}
TEMP_PATH = None


# overriding pybedtools to_dataframe method to avoid warning
def to_dataframe_fixed(self, *args, **kwargs):
    """
    Create a pandas.DataFrame, passing args and kwargs to pandas.read_csv.

    This function overrides pybedtools function to avoid FutureWarning:
    read_table is deprecated, use read_csv instead... Pandas must be
    imported as pd, it is advisable to specify dtype and names as well.
    """
    return pd.read_csv(self.fn, header=None, sep='\t', *args, **kwargs)


pbt.BedTool.to_dataframe = to_dataframe_fixed  # required for overriding


def get_name(s_file):
    """Return sample name from file path."""
    return s_file.split('/')[-1].replace('.gz', '').replace('.bed', "").replace('.xl', "")


def parse_bed6_to_df(p_file):
    """Parse BED6 file to pandas.DataFrame."""
    return pd.read_csv(
        p_file,
        names=['chrom', 'start', 'end', 'name', 'score', 'strand'],
        sep='\t',
        header=None,
        dtype={'chrom': str, 'start': int, 'end': int, 'name': str, 'score': float, 'strand': str})


def parse_region_to_df(region_file):
    """Parse GTF to pandas.DataFrame."""
    return pd.read_csv(
        region_file,
        names=['chrom', 'second', 'region', 'start', 'end', 'sixth', 'strand', 'eighth', 'id_name_biotype'],
        sep='\t',
        header=None,
        dtype={
            'chrom': str, 'second': str, 'region': str, 'start': int, 'end': int, 'sixth': str, 'strand': str,
            'eight': str, 'id_name_biotype': str})


def filter_cds_utr_ncrna(df_in):
    """Filter regions CDS, UTR5, UTR3 and ncRNA by size and trim."""
    utr5 = df_in.region == 'UTR5'
    cds = df_in.region == 'CDS'
    utr3 = df_in.region == 'UTR3'
    ncrna = df_in.region == 'ncRNA'
    size = df_in.end - df_in.start >= 100
    df_out = df_in[(utr5 & size) | (cds & size) | (utr3 & size) | ncrna].copy()
    df_out.loc[df_out['region'] == 'CDS', ['start']] = df_out.start + 30
    df_out.loc[df_out['region'] == 'CDS', ['end']] = df_out.end - 30
    return df_out


def filter_intron(df_in, min_size):
    """Filter intron regions to remove those smaller than min_size."""
    # remove regions shorter then min_size
    df_out = df_in[df_in.end - df_in.start >= min_size].copy()
    return df_out


def get_regions_map(regions_file):
    """Prepare temporary files based on GTF file that defines regions."""
    df_regions = pd.read_csv(
        regions_file, sep='\t', header=None,
        names=['chrom', 'second', 'region', 'start', 'end', 'sixth', 'strand', 'eighth', 'id_name_biotype'],
        dtype={
            'chrom': str, 'second': str, 'region': str, 'start': int, 'end': int, 'sixth': str, 'strand': str,
            'eight': str, 'id_name_biotype': str})
    df_intergenic = df_regions.loc[df_regions['region'] == 'intergenic']
    df_cds_utr_ncrna = df_regions.loc[df_regions['region'].isin(['CDS', 'UTR3', 'UTR5', 'ncRNA'])]
    df_intron = df_regions.loc[df_regions['region'] == 'intron']
    df_cds_utr_ncrna = filter_cds_utr_ncrna(df_cds_utr_ncrna)
    df_intron = filter_intron(df_intron, 100)
    to_csv_kwrgs = {'sep': '\t', 'header': None, 'index': None}
    df_intron.to_csv('{}intron_regions.bed'.format(TEMP_PATH), **to_csv_kwrgs)
    df_intergenic.to_csv('{}intergenic_regions.bed'.format(TEMP_PATH), **to_csv_kwrgs)
    df_cds_utr_ncrna.to_csv('{}cds_utr_ncrna_regions.bed'.format(TEMP_PATH), **to_csv_kwrgs)


def remove_chr(df_in, chr_sizes, chr_name='chrM'):
    """Remove chromosomes that are not in genome annotations.

    Also removes ``chr_name`` from DataFrame.
    """
    df_chr_sizes = pd.read_csv(
        chr_sizes, names=['chrom', 'end'], sep='\t', header=None, dtype={'chrom': str, 'end': int})
    df_in = df_in[df_in['chrom'].isin(df_chr_sizes['chrom'].values)]
    return df_in[~(df_in['chrom'] == chr_name)]


def intersect(interval_file, s_file):
    """Intersect two BED files and return resulting BED file."""
    if interval_file:
        result = pbt.BedTool(s_file).intersect(
            pbt.BedTool(interval_file), s=True,
            nonamecheck=True,
        ).saveas()
    else:
        result = pbt.BedTool(s_file)
    if len(result) >= 1:
        return result


def get_complement(interval_file, chrsizes_file):
    """Return BED file containing complement of peaks."""
    if '.gz' in interval_file:
        try:
            with gzip.open(interval_file, 'rb') as file:
                file.read()
        except OSError:
            print('{} has .gz in path/name but seems to not be gzipped')
            return
        interval_file_name = interval_file.split('/')[-1].replace('.gz', "")
        temp_file_interval = '{}{}.TEMPORARY'.format(TEMP_PATH, interval_file_name)
        get_sorted = (zcat[interval_file] | sort['-k1,1', '-k2,2n', '-k3,3n'])
        sorted_interval = get_sorted()
        with open(temp_file_interval, 'w') as file:
            file.write(sorted_interval)
    else:
        temp_file_interval = '{}{}.TEMPORARY'.format(TEMP_PATH, interval_file.split('/')[-1])
        sorted_file = sort('-k1,1', '-k2,2n', '-k3,3n', interval_file)
        with open(temp_file_interval, 'w') as file:
            file.write(sorted_file)
    df_interval = parse_bed6_to_df(temp_file_interval)
    df_interval = remove_chr(df_interval, chrsizes_file)
    df_interval_p = df_interval[df_interval['strand'] == '+'].copy()
    df_interval_m = df_interval[df_interval['strand'] == '-'].copy()
    interval_p = pbt.BedTool.from_dataframe(df_interval_p)
    interval_m = pbt.BedTool.from_dataframe(df_interval_m)
    temp_file = chrsizes_file + '.TEMPORARY'
    temporary_file = sort('-k1,1', '-k2,2', chrsizes_file)
    with open(temp_file, 'w') as file:
        file.write(temporary_file)
    complement_interval_p = interval_p.complement(g=temp_file)
    complement_interval_m = interval_m.complement(g=temp_file)
    df_interval_complement_p = complement_interval_p.to_dataframe(
        names=['chrom', 'start', 'end'], dtype={'chrom': str, 'start': int, 'end': int})
    df_interval_complement_m = complement_interval_m.to_dataframe(
        names=['chrom', 'start', 'end'], dtype={'chrom': str, 'start': int, 'end': int})
    df_interval_complement_p['name'] = '.'
    df_interval_complement_p['score'] = '.'
    df_interval_complement_p['strand'] = '+'
    df_interval_complement_m['name'] = '.'
    df_interval_complement_m['score'] = '.'
    df_interval_complement_m['strand'] = '-'
    df_interval_complement = pd.concat([df_interval_complement_p, df_interval_complement_m])
    df_interval_complement = df_interval_complement.sort_values(
        by=['chrom', 'start', 'strand'], ascending=[True, True, True]
    ).reset_index(drop=True)
    interval_complement = pbt.BedTool.from_dataframe(df_interval_complement)
    if interval_complement:
        return interval_complement


def cut_per_chrom(chrom, df_p, df_m, df_peaks_p, df_peaks_m):
    """Split data by strand then apply pandas cut to each strand.

    Pandas cut uses IntervalIndex (done from the peaks file) to
    assign each site its peak. Finally merges strands.
    """
    df_temp_p = df_peaks_p[df_peaks_p['chrom'] == chrom].copy()
    df_temp_m = df_peaks_m[df_peaks_m['chrom'] == chrom].copy()
    df_xl_p = df_p[df_p['chrom'] == chrom].copy()
    df_xl_m = df_m[df_m['chrom'] == chrom].copy()
    left_p = np.array(df_temp_p['start'])
    right_p = np.array(df_temp_p['end'])
    left_m = np.array(df_temp_m['start'])
    right_m = np.array(df_temp_m['end'])
    interval_index_p = pd.IntervalIndex.from_arrays(left_p, right_p, closed='left')
    interval_index_m = pd.IntervalIndex.from_arrays(left_m, right_m, closed='left')
    df_xl_p['cut'] = pd.cut(df_xl_p['start'], interval_index_p)
    df_xl_m['cut'] = pd.cut(df_xl_m['start'], interval_index_m)
    return pd.concat([df_xl_p, df_xl_m], ignore_index=True)


def cut_sites_with_region(df_sites, df_region):
    """Find peak interval the crosslinks belong to."""
    df_p = df_sites[df_sites['strand'] == '+'].copy()
    df_m = df_sites[df_sites['strand'] == '-'].copy()
    df_region_p = df_region[df_region['strand'] == '+'].copy()
    df_region_m = df_region[df_region['strand'] == '-'].copy()
    df_cut = pd.DataFrame(columns=['chrom', 'start', 'end', 'name', 'score', 'strand', 'feature', 'attributes', 'cut'])
    for chrom in set(df_region['chrom'].values):
        df_temp = cut_per_chrom(chrom, df_p, df_m, df_region_p, df_region_m)
        df_temp = df_temp[df_cut.columns]
        df_cut = pd.concat([df_cut, df_temp], ignore_index=True)
    return df_cut.dropna(axis=0)


def percentile_filter_xlinks(df_in, percentile=0.7):
    """Calculate threshold and filter sites by it."""
    df_in['cut'] = df_in['cut'].astype(str)
    df_in['quantile'] = df_in['cut'].map(df_in.groupby('cut').quantile(q=percentile)['score'])
    df_in = df_in[df_in['score'] > df_in['quantile']]
    return df_in[['chrom', 'start', 'end', 'name', 'score', 'strand', 'feature', 'attributes']]


def intersect_merge_info(region, s_file):
    """Intersect while keeping information from region file."""
    interval_file = REGIONS_MAP[region]
    try:
        df_1 = intersect(interval_file, s_file).to_dataframe(
            names=['chrom', 'start', 'end', 'name', 'score', 'strand'],
            dtype={'chrom': str, 'start': int, 'end': int, 'name': str, 'score': float, 'strand': str})
        df_1 = df_1.groupby(['chrom', 'start', 'end', 'strand'], as_index=False)['score'].sum(axis=0)
        df_1['name'] = '.'
        df_2 = intersect(s_file, interval_file).to_dataframe(
            names=['seqname', 'source', 'feature', 'start', 'end', 'score', 'strand', 'frame', 'attributes'],
            dtype={'seqname': str, 'source': str, 'feature': str, 'start': int, 'end': int, 'score': str,
                   'strand': str, 'frame': str, 'attributes': str})
        df_2.drop_duplicates(subset=['seqname', 'start', 'end', 'strand'], keep='first')
    except AttributeError:
        return
    df_2 = df_2.drop(columns=['source', 'score', 'frame', 'start']).rename(index=str, columns={"seqname": "chrom"})
    return pd.merge(df_1, df_2, on=['chrom', 'strand', 'end'])


def get_threshold_sites(s_file, percentile=0.7):
    """Apply crosslink filtering based on dynamical thresholds.

    Regions for thresholds are defined as follows: introns and
    intergenic regions are each idts own region, for CDS, UTR and ncRNA
    each gene is a region. After region determination threshold based on
    percentile are applied and finally threshold crosslinks sites are
    sorted.
    """
    df_out = pd.DataFrame(columns=['chrom', 'start', 'end', 'name', 'score', 'strand', 'feature', 'attributes'])
    for region in REGIONS_QUANTILE:
        print(f'Thresholding {region}')
        region_threshold_cp = time.time()
        df_reg = intersect_merge_info(region, s_file)
        if df_reg is None:
            return
        print(f'lenght of df_reg for {region} is: {len(df_reg)}')
        if region == 'cds_utr_ncrna':
            df_reg.name = df_reg.attributes.map(lambda x: x.split(';')[1].split(' ')[1].strip('"'))
            df_reg['quantile'] = df_reg['name'].map(df_reg.groupby(['name']).quantile(q=percentile)['score'])
            df_filtered = df_reg[df_reg['score'] > df_reg['quantile']].drop(columns=['quantile'])
            df_out = pd.concat([df_out, df_filtered], ignore_index=True, sort=False)
        if region in ['intron', 'intergenic']:
            df_region = parse_region_to_df(REGIONS_MAP[region])
            df_cut = cut_sites_with_region(df_reg, df_region)
            df_filtered = percentile_filter_xlinks(df_cut)
            df_out = pd.concat([df_out, df_filtered], ignore_index=True, sort=False)
        print(f'Thresholding {region} runtime: {((time.time() - region_threshold_cp) / 60):.2f} min')
    return df_out.sort_values(by=['chrom', 'start', 'strand'], ascending=[True, True, True]).reset_index(drop=True)


def get_all_sites(s_file):
    """Get crosslink data into appropriate dataframe without thresholding."""
    df_out = pd.DataFrame(columns=['chrom', 'start', 'end', 'name', 'score', 'strand', 'feature', 'attributes'])
    for region in REGIONS_QUANTILE:
        df_reg = intersect_merge_info(region, s_file)
        if df_reg.empty:
            continue
        if region == 'cds_utr_ncrna':
            df_reg.name = df_reg.attributes.map(lambda x: x.split(';')[1].split(' ')[1].strip('"'))
            df_reg['quantile'] = None
            df_out = pd.concat([df_out, df_reg], ignore_index=True, sort=False)
        if region in ['intron', 'intergenic']:
            df_region = parse_region_to_df(REGIONS_MAP[region])
            df_cut = cut_sites_with_region(df_reg, df_region)
            df_filtered = df_cut[['chrom', 'start', 'end', 'name', 'score', 'strand', 'feature', 'attributes']]
            df_out = pd.concat([df_out, df_filtered], ignore_index=True, sort=False)
    return df_out.sort_values(by=['chrom', 'start', 'strand'], ascending=[True, True, True]).reset_index(drop=True)


def get_sequences(sites, fasta, fai, window_l, window_r, merge_overlaps=False):
    """Get genome sequences around positions defined in sites."""
    sites = pbt.BedTool(sites).sort()
    sites_extended = sites.slop(l=window_l, r=window_r, g=fai)  # noqa
    if merge_overlaps:
        sites_extended = sites_extended.merge(s=True)
    seq_tab = sites_extended.sequence(s=True, fi=fasta, tab=True)
    return [line.split("\t")[1].strip() for line in open(seq_tab.seqfn)]


def count_kmers(sequences, k_length):
    """Get number of occurrences of each kmer in a list of sequences."""
    possible_kmers = []
    for i in product('ACGT', repeat=k_length):
        possible_kmers.append("".join(i))
    kmers = {el: 0 for el in possible_kmers}
    for sequence in sequences:
        for i in range(len(sequence) - k_length + 1):
            try:
                kmers[sequence[i: i + k_length]] += 1
            except KeyError:
                pass
    return kmers


def pos_count_kmer(seqs, k_length, window, kmer_list=False):
    """Get number of occurences of each kmer for each position.

    Alternativly, if kmer_list is defined, it returns positional counts
    only for kmers in the list.
    """
    shift = int((k_length + 1) / 2)
    zero_counts = {pos: 0 for pos in range(-window + shift, window + shift + 1)}
    if kmer_list:
        possible_kmers = kmer_list
    else:
        possible_kmers = []
        for i in product('ACGT', repeat=k_length):
            possible_kmers.append("".join(i))
    kmer_pos_count = {x: zero_counts.copy() for x in possible_kmers}
    for sequence in seqs:
        for i in range(k_length, len(sequence) - k_length):
            kmer = sequence[i: i + k_length]
            relative_pos = i - window - k_length + shift
            try:
                kmer_pos_count[kmer][relative_pos] += 1
            except KeyError:
                pass
    return kmer_pos_count


def normalise_kmer_frequency(observed, reference):
    """Normalize kmer counts - divide observed with reference counts."""
    normalised = {}
    for kmer, count in observed.items():
        # In short regions of the reference there could be 0 of certain kmers.
        # In such case, just normalize with 1.
        try:
            normalised[kmer] = count / reference[kmer] * 10 ** 6
        except ZeroDivisionError:
            normalised[kmer] = count * 10 ** 6
    return normalised


def get_max_pos(pos_count, window_peak_l=15, window_peak_r=15):
    """Return position with max values for every kmer in the dictionary."""
    max_pos = {}
    pc_peak = {}
    for motif, pos_c in pos_count.items():
        pc_peak[motif] = {x: pos_c[x] for x in range(-abs(window_peak_l), window_peak_r + 1)}
    for motif, pos in pc_peak.items():
        max_pos[motif] = max(pos, key=pos.get)
    return max_pos


def get_subcounts(pos_c, max_p, ext=5):
    """Return shrunk positional distribution.

    That is  from -ext to +ext around max value as defined in mp.
    """
    pos_c_out = {x: {} for x in pos_c}
    for key, value in pos_c.items():
        max_pos = max_p[key]
        max_range = max(value)
        min_range = min(value)
        if max_pos < (min_range + ext):
            window = range(min_range, min_range + 2 * ext + 1)
        elif max_pos > (max_range - ext):
            window = range(max_range - 2 * ext, max_range + 1)
        else:
            window = range(max_pos - ext, max_pos + ext + 1)
        for win in window:
            pos_c_out[key][win] = value[win]
    return pos_c_out


def mask_positions(pos_c, k_length, mask_l=100, mask_r=100):
    """Return positional counts with removed positions around crosslinks."""
    shift = int((k_length + 1) / 2)
    mask = list(range(-mask_l + shift, mask_r + shift))
    for _, value in pos_c.items():
        for pos in mask:
            value.pop(pos, None)
    return pos_c


def get_average_poscount(pos_c):
    """Return average of positional counts."""
    avg = {}
    for key, value in pos_c.items():
        avg[key] = sum(value.values()) / len(value)
    total_counts = sum(avg.values())
    for key, value in avg.items():
        try:
            avg[key] = value / total_counts
        except ZeroDivisionError:
            avg[key] = value
    return avg


def get_top_n_kmers(kmer_count, num):
    """Get a list of top_n most frequent kmers."""
    return [item[0] for item in sorted(kmer_count.items(), key=lambda x: x[1], reverse=True)[:num]]


def get_clustering(kmer_pos_count, clustering_pm, smoot=6, clust=3):
    """Smoothen positional data for each kmer and then cluster kmers.

    Prior to clustering PCA is ran to reduce number of dimensions. Return smooth
    dataframe and a dictionary of cluster with belonging kmers.
    """
    # read kmer_pos_count dictionary into a data frame
    df_in = pd.DataFrame(kmer_pos_count)
    # smoothen
    df_smooth = df_in.rolling(smoot, center=True, win_type='triang').mean()
    # slicing drops edge values that get NaN due to rolling mean
    df_smooth = df_smooth.iloc[int(smoot / 2): -(int(smoot / 2) + 1), :]
    df_t = df_smooth.T
    df_cl = pd.DataFrame(clustering_pm).T
    df_cl = df_cl[df_cl.index.isin(df_t.index)]
    pca = PCA(n_components=4, svd_solver='full')
    principal_components = pca.fit_transform(df_cl)
    principal_df = pd.DataFrame(
        data=principal_components,
        columns=['principal component 1', 'principal component 2', 'principal component 3', 'principal component 4'])
    kmeans = KMeans(n_clusters=clust, random_state=4242).fit(principal_df)
    # append lists of kmers belonging to each cluster
    df_map = pd.DataFrame()
    df_map['data_index'] = df_cl.index.values
    df_map['cluster'] = kmeans.labels_
    c_dict = {}
    for i in range(clust):
        c_dict['cluster' + str(i)] = df_map[df_map.cluster == i].set_index('data_index').index.values
    return df_smooth, c_dict


def substrings(string):
    """Return set of substrings of a string."""
    return {string[x:y] for x, y in combinations(range(len(string) + 1), r=2)}


def get_all_substrings(string_list):
    """Return set of all substring in a list of string."""
    return {item for subset in [substrings(string) for string in string_list] for item in subset}


def find_common_substrings(substring_set, string_list):
    """Return set substring common to all strings in a list of strings."""
    return {s for s in substring_set if all(s in sublist for sublist in string_list)}


def get_longest_substrings(string_set):
    """Return list of strings of maximal length in a set of strings."""
    longest = len(max(string_set, key=lambda x: len(x)))
    return [x for x in string_set if len(x) == longest]


def get_index(substring, kmer_list):
    """Return set of indices of positions of substrings in a list of strings."""
    return {k: k.find(substring) for k in kmer_list}


def get_matrices(longest_substring, kmer_list):
    """Cunstruct a matrix representing aligned and padded strings."""
    matrix = {}
    for substring in longest_substring:
        long_sub_index = get_index(substring, kmer_list)
        sorted_index_dict = {
            k: long_sub_index[k] for k in sorted(long_sub_index, key=long_sub_index.get, reverse=True)
        }
        first = sorted_index_dict[list(sorted_index_dict.keys())[0]]
        padded = []
        for key, value in sorted_index_dict.items():
            k_to_list = list(key)
            for _ in range(first - value):
                k_to_list.insert(0, '0')
            padded.append(k_to_list)
        longest = len(max(padded, key=lambda x: len(x)))
        for j in padded:
            while len(j) < longest:
                j.append('0')
        matrix[substring] = padded
    return matrix


def get_consensus(padded):
    """Return consensus from matrix of aligned sequences."""
    seq = {x: {'A': 0, 'C': 0, 'G': 0, 'U': 0} for x in range(len(padded[0]))}
    for kmer_split in padded:
        for pos, base in enumerate(kmer_split):
            try:
                seq[pos][base] += 1
            except KeyError:
                pass
    consensus_positions = {x: [] for x in seq.keys()}
    for pos, bases in seq.items():
        max_count = max(bases.values())
        max_count_bases = [base for base in bases.keys() if bases[base] == max_count]
        consensus_positions[pos].extend(max_count_bases)
    count_per_pos = {}
    for key, value in seq.items():
        count_per_pos[key] = max(value.values())
    max_count_pos = []
    max_count_p = max(count_per_pos.values())
    for key, value in count_per_pos.items():
        if value == max_count_p:
            max_count_pos.append(key)
    seed = []
    for pos in range(max_count_pos[0], max_count_pos[-1] + 1):
        if len(seed) <= 5:
            seed.append(pos)
    counter = 0
    while len(seed) < 5 and counter < 6:
        if count_per_pos.get(seed[0] - 1, 0) > count_per_pos.get(seed[-1] + 1, 0):
            seed.insert(0, seed[0] - 1)
        elif count_per_pos.get(seed[0] - 1, 0) < count_per_pos.get(seed[-1] + 1, 0):
            seed.append(seed[-1] + 1)
        elif count_per_pos.get(seed[0] - 1, 0) == count_per_pos.get(seed[-1] + 1, 0):
            if count_per_pos.get(seed[0] - 1, 0) >= 2:
                seed.insert(0, seed[0] - 1)
                seed.append(seed[-1] + 1)
        counter += 1
    consensus = [consensus_positions[pos] for pos in seed]
    return consensus


def chose_best_consensus(consensuses, kmer_list):
    """Return best consensus found in the list of consensuses."""
    if len(consensuses) == 1:
        return consensuses[0]
    score_dict = {}
    for i, consensus in enumerate(consensuses):
        score = 0
        for combo in product(*consensus):
            for kmer in kmer_list:
                if ''.join(combo) in kmer:
                    score += 1
        score_dict[i] = score
    max_score = max(score_dict.values())
    top_scored = [consensuses[k] for k, v in score_dict.items() if v == max_score]
    if len(top_scored) == 1:
        return top_scored[0]
    for kmer in kmer_list:
        for cons in top_scored:
            cons_flat = [i[0] for i in cons]
            if ''.join(cons_flat) in kmer:
                return cons
            cons_minus1start = cons[1:]
            cons_minus1start_flat = [i[0] for i in cons_minus1start]
            if ''.join(cons_minus1start_flat) in kmer:
                return cons_minus1start
            cons_minus1end = cons[:-1]
            cons_minus1end_flat = [i[0] for i in cons_minus1end]
            if ''.join(cons_minus1end_flat) in kmer:
                return cons_minus1end
            cons_minus1startend = cons[1:-1]
            cons_minus1startend_flat = [i[0] for i in cons_minus1startend]
            if ''.join(cons_minus1startend_flat) in kmer:
                return cons_minus1startend
            cons_minus2start = cons[2:]
            cons_minus2start_flat = [i[0] for i in cons_minus2start]
            if ''.join(cons_minus2start_flat) in kmer:
                return cons_minus2start
            cons_minus2end = cons[:-2]
            cons_minus2end_flat = [i[0] for i in cons_minus2end]
            if ''.join(cons_minus2end_flat) in kmer:
                return cons_minus2end
            return kmer_list[0]


def get_clusters_name(c_dict):
    """Try to find a consensus sequence in a cluster of kmers.

    When not possible returns the bases of most enriched kmer. In case of
    duplicate names '_1' is appended to each duplicate.
    """
    c_con_dict = {}
    for cluster_id, kmers_list in c_dict.items():
        if len(kmers_list) == 1:
            # if there is only one kmer in a cluster than cluster name is kmer
            c_con_dict[cluster_id] = kmers_list[0]
        elif len(kmers_list) > 1:
            all_substrings = get_all_substrings(kmers_list)
            common_substrings = find_common_substrings(all_substrings, kmers_list)
            if not common_substrings:
                c_con_dict[cluster_id] = kmers_list[0]
            else:
                longest_subtring = get_longest_substrings(common_substrings)
                matrices = get_matrices(longest_subtring, kmers_list)
                consensuses = []
                for matrix in matrices.values():
                    consensuses.append(get_consensus(matrix))
                consensus_list = chose_best_consensus(consensuses, kmers_list)
                final_list = []
                for base in consensus_list:
                    if len(base) == 1:
                        final_list.append(base[0])
                    elif len(base) > 1:
                        final_list.append(f'[{"".join(base)}]')
                final_str = ''.join(final_list).replace('ACGU', 'N')
                if len(final_list) == 1:
                    c_con_dict[cluster_id] = kmers_list[0]
                elif final_list and (final_str not in c_con_dict.values()):
                    c_con_dict[cluster_id] = final_str
                elif final_list and (final_str in c_con_dict.values()):
                    while final_str in c_con_dict.values():
                        final_str += ('_1')
                    c_con_dict[cluster_id] = final_str
                elif not final_list:
                    c_con_dict[cluster_id] = kmers_list[0]
    return c_con_dict


def get_cluster_wide_sum(topkmer_pos_count, c_dict):
    """Calculate average positional distribution for each cluster."""
    df_in = pd.DataFrame(topkmer_pos_count)
    clusters = []
    # for each cluster, calculate sum of occurences at each position
    for cluster, motif in c_dict.items():
        df_cluster = df_in[motif].copy()
        df_cluster[cluster] = df_cluster.sum(axis=1)
        clusters.append(df_cluster[cluster])
    return pd.concat(clusters, axis=1).rolling(5, center=True).mean().dropna()


def plot_positional_distribution(df_in, df_sum, c_dict, c_rank, name, cluster_rename, region):
    """Plot each cluster on its own plot.

    Also, plot combining the averages of clusters over a larger window.
    """
    c_num = len(c_dict)
    num_rows = int(np.ceil((c_num + 1) / 2)) if c_num > 1 else 2
    sns.set(rc={'figure.figsize': (24, num_rows * 7)})
    fig, axs = plt.subplots(nrows=num_rows, ncols=2)
    plt.subplots_adjust(left=None, bottom=None, right=None, top=None, wspace=None, hspace=0.3)
    fig.suptitle(f'{name}_{region}', fontsize=20)
    lineplot_kwrgs = {'palette': "tab10", 'linewidth': 1, 'dashes': False, }
    xlabel = 'Positions of kmer start relative to crosslinks'
    ylabel = 'Kmer occurence per thresholded crosslinks (%)'
    rank_c = {y: x for x, y in c_rank.items()}
    rank_ordered = OrderedDict(sorted(rank_c.items()))
    # plot clusters in order starting from cluster with highest average max
    # enrichement
    for rank, cluster in rank_ordered.items():
        # define position of subplot
        axs_x = (rank - 1) // 2
        axs_y = (rank - 1) % 2
        # change name to consensus sequence
        c_name = cluster_rename[cluster]
        axs[axs_x, axs_y].set(xlabel=xlabel, ylabel=ylabel, title='Cluster of kmers {}'.format(c_name))
        df_plot = df_in[c_dict[cluster]]
        df_plot = df_plot[df_plot.index.isin(range(-50, 51))]
        sns.lineplot(data=df_plot, ax=axs[axs_x, axs_y], ci=None, **lineplot_kwrgs)
    # final plot of summed clusters in a wider window
    df_ordered = df_sum[list(rank_ordered.values())].rename(columns=cluster_rename)
    axs_x_sumplt = c_num // 2
    axs_y_sumplt = c_num % 2
    axs[axs_x_sumplt, axs_y_sumplt].set(
        xlabel=xlabel, ylabel='Kmer cluster occurence (%)', title='Summed occurrence of kmers in each cluster')
    axs[axs_x_sumplt, axs_y_sumplt].set_xlim(-150, 100)
    sns.lineplot(data=df_ordered, ax=axs[axs_x_sumplt, axs_y_sumplt], ci=None, **lineplot_kwrgs)
    fig.savefig(f'./results/{name}_{region}.pdf', format='pdf')


def run(peak_file, sites_file, genome, genome_fai, regions_file, window, window_distal, kmer_length, top_n,
        percentile, min_relativ_occurence, clusters, smoothing, all_outputs=False, regions=None):
    """Start the analysis.

    Description of parameters:
    - peak_file: intervals of crosslinks in BED file format
    - sites_file: crosslinks in BED file format
    - genome: FASTA file format, preferably the same as was used for alignment
    - genome_fai: FASTA index file
    - regions_file: custom genome segmentation file
    - window: region around (thresholded) crosslinks where positional
      distributions are obtained by counting kmers per position (default 40)
    - window_distal: region considered for background distribution (default 150)
    - kmer_length: length (in nucleotides) of kmers to be analysed (default 4,
      with option between 3 and 7)
    - top_n: number of kmers ranked by z-score in descending order for
      clustering and plotting (default 20)
    - percentile: used for thresholding crosslinks (default 0.7)
    - min_relative_occurence: ratio of kmer distribution around (thresholded)
      crosslinks to distal occurrences (default 2)
    - clusters: number of clusters of kmers(default 5)
    - smoothing: window used for smoothing kmer positional distribution curves
    (default 6)
    - all_outputs: controls the amount of outputs produced in the analysis
    """
    start = time.time()
    if regions is None:
        regions = REGIONS
    assert set(regions).issubset(set(REGIONS))
    sample_name = get_name(sites_file)
    global TEMP_PATH
    TEMP_PATH = './TEMP{}/'.format(randint(10 ** 6, 10 ** 7))
    os.makedirs(TEMP_PATH)
    os.makedirs('./results/', exist_ok=True)
    get_regions_map(regions_file)
    global REGIONS_MAP
    REGIONS_MAP = {
        'intron': '{}intron_regions.bed'.format(TEMP_PATH),
        'intergenic': '{}intergenic_regions.bed'.format(TEMP_PATH),
        'cds_utr_ncrna': '{}cds_utr_ncrna_regions.bed'.format(TEMP_PATH)}
    print('Getting thresholded crosslinks')
    df_txn = get_threshold_sites(sites_file, percentile=percentile)
    print(f'Thresholding runtime: {((time.time() - start) / 60):.2f} min for {len(df_txn)} thresholded crosslinks')
    if df_txn is None:
        print("Not able to find any thresholded sites.")
        return
    genome_chr_sizes = '{}genome.sizes'.format(TEMP_PATH)
    cut = local["cut"]
    make_genome_sz = cut("-f1,2", genome_fai)
    with open(genome_chr_sizes, 'w') as file:
        file.write(make_genome_sz)
    df_txn = remove_chr(df_txn, '{}genome.sizes'.format(TEMP_PATH))
    checkpoint1 = time.time()
    df_xn = get_all_sites(sites_file)
    print(f'{len(df_xn)} total sites. All sites taging runtime: {((time.time() - checkpoint1) / 60):.2f} min')
    for region in regions:
        region_start = time.time()
        # Parse sites file and keep only parts that intersect with given region
        df_sites = df_txn.loc[df_txn['feature'].isin(REGION_SITES[region])]
        print(f'{len(df_sites)} thresholded sites on {region}')
        df_xn_region = df_xn.loc[df_xn['feature'].isin(REGION_SITES[region])]
        print(f'{len(df_xn_region)} all sites on {region}')
        sites = pbt.BedTool.from_dataframe(
            df_sites[['chrom', 'start', 'end', 'name', 'score', 'strand']])
        if all_outputs:
            sites.saveas('./results/{}_threshold_crosslinks_{}.bed'.format(sample_name, region))
        # only continue analysis for region with over 100 thresholded sites
        if len(sites) < 100:
            print(f'less then 100 thresholded crosslink in {region}')
            continue
        all_sites = pbt.BedTool.from_dataframe(df_xn_region[['chrom', 'start', 'end', 'name', 'score', 'strand']])
        # finds all crosslink sites that are not in peaks as reference for
        # normalization
        complement = get_complement(peak_file, '{}genome.sizes'.format(TEMP_PATH))
        # if region == 'whole_gene':
        #     complement = intersect(REGIONS_MAP['whole_gene_reference'], complement)
        reference = intersect(complement, all_sites)
        noxn = len(reference)
        print(f'noxn {noxn} on {region}')
        ntxn = len(sites)
        print(f'ntxn {ntxn} on {region}')
        if all_outputs:
            reference.saveas(f'./results/{sample_name}_oxn_{region}.bed')
        # get sequences around all crosslinks not in peaks
        reference_sequences = get_sequences(
            reference, genome, genome_fai, window + kmer_length, window + kmer_length, merge_overlaps=False)
        # get sequences around all thresholded crosslinks
        sequences = get_sequences(sites, genome, genome_fai, window_distal + kmer_length, window_distal + kmer_length)
        get_sequences_cp = time.time()
        # get positional counts for all kmers around thresholded crosslinks
        kmer_pos_count_t = pos_count_kmer(sequences, kmer_length, window_distal)
        print(f'Kmer positional counting runtime: {((time.time() - get_sequences_cp) / 60):.2f} min')
        kmer_pos_count = {key.replace('T', 'U'): value for key, value in kmer_pos_count_t.items()}
        # get position where the kmer count is maximal
        max_p = get_max_pos(kmer_pos_count, window_peak_l=15, window_peak_r=15)
        # prepare dataframe for outfile
        df_out = pd.DataFrame.from_dict(max_p, orient='index', columns=['mtxn'])
        # get kmer counts in distal areas of thresholded crosslinks
        kmer_pc_copy = copy.deepcopy(kmer_pos_count)
        distal = mask_positions(kmer_pc_copy, kmer_length)
        # calculate average distal occurences of kmers
        avg_distal_occ = {}
        for key, value in distal.items():
            avg_distal_occ[key] = sum(value.values()) / len(value)
        # occurences of kmers on each position around thresholded crosslinks
        # relative to distal occurences
        rtxn = {x: {} for x in kmer_pos_count}
        for motif, pos_m in kmer_pos_count.items():
            for pos, count in pos_m.items():
                try:
                    rtxn[motif][pos] = count / avg_distal_occ[motif]
                except ZeroDivisionError:
                    rtxn[motif][pos] = count
        rtxn_cp = time.time()
        # get positional counts for all kmers around all crosslink not in peaks
        ref_pc_t = pos_count_kmer(reference_sequences, kmer_length, window)
        print(f'Reference positional counts runtime: {((time.time() - rtxn_cp) / 60):.2f} min')
        ref_pc = {key.replace('T', 'U'): value for key, value in ref_pc_t.items()}
        # occurences of kmers on each position around all crosslinks not in
        # peaks (reference) relative to distal occurences
        roxn = {x: {} for x in ref_pc}
        for motif, pos_m in ref_pc.items():
            for pos, count in pos_m.items():
                try:
                    roxn[motif][pos] = (count * ntxn) / (avg_distal_occ[motif] * noxn)
                except ZeroDivisionError:
                    roxn[motif][pos] = (count * ntxn) / noxn
        # get all positions around thresholded crosslinks between -60 and 60
        # where relative occurence is higher then an arbitrary value (minimal
        # relative occurence), default 2
        prtxn = {x: [] for x in rtxn}
        window_inner = int(window / 3)
        relevant_pos_inner = list(
            range(-window_inner + int((kmer_length + 1) / 2), window_inner + 1 + int((kmer_length + 1) / 2)))
        relevant_pos_outer = list(range(-window + int((kmer_length + 1) / 2), window + 1 + int((kmer_length + 1) / 2)))
        for i in relevant_pos_outer:
            if i in relevant_pos_inner:
                for kmer, posm in rtxn.items():
                    prtxn[kmer].append(i)
            else:
                for kmer, posm in rtxn.items():
                    if posm[i] > min_relativ_occurence:
                        prtxn[kmer].append(i)
        # prepare relevant positions obtained from previous step for output
        # table and add it to the output table
        prtxn_concat = {}
        for key, value in prtxn.items():
            prtxn_concat[key] = ', '.join([str(i) for i in value])
        df_prtxn = pd.DataFrame.from_dict(prtxn_concat, orient='index', columns=['prtxn'])
        df_out = pd.merge(df_out, df_prtxn, left_index=True, right_index=True)
        prtxn_cp = time.time()
        # for z-score calculation random samples from crosslink out of peaks
        # (reference) are used and for each sample we calculate average relative
        # occurences for each kmer on relevant positions and add them to a list
        # for calculation of averages and standard deviations
        random_aroxn = []
        for _ in range(100):
            random_seqs = random.sample(reference_sequences, len(sites))
            random_kmer_pos_count_t = pos_count_kmer(random_seqs, kmer_length, window)
            random_kmer_pos_count = {key.replace('T', 'U'): value for key, value in random_kmer_pos_count_t.items()}
            roxn_sample = {x: {} for x in random_kmer_pos_count}
            for motif, pos_m in random_kmer_pos_count.items():
                for pos, count in pos_m.items():
                    try:
                        roxn_sample[motif][pos] = count / avg_distal_occ[motif]
                    except ZeroDivisionError:
                        roxn_sample[motif][pos] = count
            aroxn_sample = {x: np.mean([roxn_sample[x][y] for y in prtxn[x]]) for x in roxn_sample}
            random_aroxn.append(aroxn_sample)
        print(f'Analysing random samples runtime: {((time.time() - prtxn_cp) / 60):.2f} min')
        # calculate average relative occurences for each kmer around thresholded
        # crosslinks across relevant positions and add it to outfile table
        artxn = {x: np.mean([rtxn[x][y] for y in prtxn[x]]) for x in rtxn}
        df_artxn = pd.DataFrame.from_dict(artxn, orient='index', columns=['artxn'])
        df_out = pd.merge(df_out, df_artxn, left_index=True, right_index=True)
        # calculate average relative occurences for each kmer around reference
        # crosslinks across relevant positions and add it to outfile table
        aroxn = {x: np.mean([roxn[x][y] for y in prtxn[x]]) for x in roxn}
        df_aroxn = pd.DataFrame.from_dict(aroxn, orient='index', columns=['aroxn'])
        df_out = pd.merge(df_out, df_aroxn, left_index=True, right_index=True)
        # calculate log2 of ratio between average relative occurences between
        # thresholded and reference crosslinks, this ratio, colaculated for each
        # kmer is called enrichement and is added to outfile table
        artxn = {x: artxn[x] for x in artxn if not np.isnan(artxn[x])}
        etxn = {x: np.log2(artxn[x] / aroxn[x]) for x in artxn}
        df_etxn = pd.DataFrame.from_dict(etxn, orient='index', columns=['etxn'])
        df_out = pd.merge(df_out, df_etxn, left_index=True, right_index=True, how='outer')
        # average relative occurence obtained with random sampling are combined
        # in a structure that can be then used for calculating averages,
        # standard deviations and finaly the z-score
        combined_aroxn = {}
        for sample in random_aroxn:
            for key, value in sample.items():
                values_list = combined_aroxn.get(key, [])
                values_list.append(value)
                combined_aroxn[key] = values_list
        random_avg = {}
        random_std = {}
        for key, value in combined_aroxn.items():
            random_avg[key] = np.mean(value)
            random_std[key] = np.std(value)
        z_score = {}
        for key, value in random_avg.items():
            try:
                z_score[key] = (artxn[key] - value) / random_std[key]
            except KeyError:
                print(f'Warning: {key} missing from artxn')
        df_z_score = pd.DataFrame.from_dict(z_score, orient='index', columns=['z-score'])
        df_out = pd.merge(df_out, df_z_score, left_index=True, right_index=True, how='outer')
        # using z-score we can also calculate p-values for each motif which are
        # then added to outfile table
        df_out['p-value'] = scipy.special.ndtr(-df_out['z-score'])
        # kmer positional occurences around thresholded crosslinks on positions
        # around -50 to 50 are also added to outfile table which is then finnaly
        # written to file
        # get order of z-scores to select top kmers to plot
        kmers_order_of_enrichment = get_top_n_kmers(z_score, 4**kmer_length)
        top_kmers = kmers_order_of_enrichment[:top_n]
        # normalize kmer occurences by number of thresholded crosslinks for
        # easier comparison across different samples
        ntxn = len(sites)
        kmer_occ_per_txl = {x: {} for x in kmer_pos_count}
        for motif, pos_m in kmer_pos_count.items():
            for pos, count in pos_m.items():
                kmer_occ_per_txl[motif][pos] = count * 100 / ntxn
        df_kmer_occ_per_txl = pd.DataFrame.from_dict(kmer_occ_per_txl, orient='index')
        exported_columns = [i for i in range(-48, 51)]
        df_kmer_occ_per_txl = df_kmer_occ_per_txl[exported_columns]
        df_out = pd.merge(df_out, df_kmer_occ_per_txl, left_index=True, right_index=True, how='outer')
        df_out.to_csv(f'./results/{sample_name}_{kmer_length}mer_{region}.tsv', sep='\t', float_format='%.8f')
        kmer_occ_per_txl_ln = {x: {} for x in kmer_occ_per_txl}
        for motif, pos_m in kmer_occ_per_txl.items():
            for pos, count in pos_m.items():
                if pos in range(-48, 51):
                    kmer_occ_per_txl_ln[motif][pos] = np.log(count + 1)
        plot_selection_unsorted = {kmer: values for kmer, values in kmer_occ_per_txl.items() if kmer in top_kmers}
        plot_selection = {k: plot_selection_unsorted[k] for k in top_kmers}
        df_smooth, clusters_dict = get_clustering(plot_selection, kmer_occ_per_txl_ln, smoothing, clusters)
        # for meta analysis clusters are also output in a file
        with open(f'./results/{sample_name}_{region}_clusters.csv', 'w', newline='') as file:
            writer = csv.writer(file, lineterminator='\n')
            for key, val in clusters_dict.items():
                writer.writerow([key, val])
        # calculating average occurences for the last plot that displays average
        # occurences for each cluster over wider window, also output as a file
        df_cluster_sum = get_cluster_wide_sum(plot_selection, clusters_dict)
        sum_name = '{}_sum_cluster_distribution_{}.tsv'.format(sample_name, region)
        # find cluster with max average peak value, rank clusters by this value
        # and plot clusters in order using thie rank
        clusters_max = {cluster: max(df_cluster_sum[cluster]) for cluster in df_cluster_sum.columns}
        clusters_rank = {
            key: rank for rank, key in enumerate(sorted(clusters_max, key=clusters_max.get, reverse=True), 1)}
        # using positions and occurences each cluster gets a name
        cluster_rename = get_clusters_name(clusters_dict)
        df_cluster_sum.rename(columns=cluster_rename).to_csv('./results/' + sum_name, sep='\t')
        # finnaly plot all the clusters and the wider window (-150 to 100) plot
        # with average occurences
        plot_positional_distribution(
            df_smooth, df_cluster_sum, clusters_dict, clusters_rank, sample_name, cluster_rename, region)
        plot_cp = time.time()
        print(f'Analysing {region} runtime: {((plot_cp - region_start) / 60):.2f}')
        print(f'Analysing {region} in seconds per thresholded_crosslink: {(plot_cp - region_start) / ntxn}')
    # cleanup temporary files
    shutil.rmtree(TEMP_PATH)
    pbt.cleanup()
    print(f'Analysis total runtime {((time.time() - start) / 60):.2f}')
