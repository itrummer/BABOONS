'''
Created on Jun 6, 2021

@author: immanueltrummer
'''
import argparse
import cp.base
import cp.bench
import cp.cache.dynamic
import cp.cache.static
import cp.rl
import logging
import psycopg2
import time

from stable_baselines3 import A2C, PPO
from cp.pred import all_preds

def print_details(env):
    """ Prints details about RL results.
    
    Args:
        env: extract info from this environment
    """
    best_summary = env.best_summary()
    print(f'Best summary: "{best_summary}"')
    for best in [True, False]:
        print(f'Top-K summaries (Best: {best})')
        topk_summaries = env.topk_summaries(2, best)
        for s in topk_summaries:
            print(s)
            
    print('Facts generated:')
    for f in env.fact_to_text.values():
        print(f'{f}')


def run_rl(connection, test_case, all_preds, nr_samples, c_type, c_freq):
    """ Benchmarks primary method on test case.
    
    Args:
        connection: connection to database
        test_case: describes test case
        all_preds: ordered predicates
        nr_samples: number of iterations
        cache_freq: frequency of cache updates
        
    Returns:
        summaries with reward, performance statistics
    """
    start_s = time.time()
    
    table = test_case['table']
    if c_type == 'dynamic':
        cache = cp.cache.dynamic.DynamicCache(connection, table, c_freq, False)
    elif c_type == 'empty':
        cache = cp.cache.static.EmptyCache()
    elif c_type == 'cube':
        dim_cols = test_case['dim_cols']
        cmp_pred = test_case['cmp_pred']
        agg_cols = test_case['agg_cols']
        cache = cp.cache.static.CubeCache(
            connection, table, dim_cols, cmp_pred, agg_cols, 900)
    else:
        raise ValueError(f'Unknown cache type: {c_type}')
    
    env = cp.rl.PickingEnv(
        connection, **test_case, 
        all_preds=all_preds, cache=cache)
    model = A2C(
        'MlpPolicy', env, verbose=True, 
        gamma=1.0, normalize_advantage=True)
    model.learn(total_timesteps=nr_samples)
    total_s = time.time() - start_s
    logging.debug(f'Optimization took {total_s} seconds')
    
    p_stats = {'time':total_s}
    p_stats.update(env.statistics())
    
    return env.s_eval.text_to_reward, p_stats


def run_random(connection, test_case, all_preds, nr_sums, timeout_s):
    """ Run simple random generation baseline.
    
    Args:
        connection: connection to database
        test_case: summarize for this test case
        all_preds: all available predicates
        nr_sums: generate so many summaries
        timeout_s: sample until this timeout
        
    Returns:
        summaries with quality, performance statistics
    """
    return cp.base.rand_sums(
        nr_sums=nr_sums, timeout_s=timeout_s, 
        connection=connection, all_preds=all_preds,
        **test_case)


def run_gen(connection, test_case, all_preds, timeout_s):
    """ Run generative learning baseline.
    
    Args:
        connection: connection to database
        test_case: summarize for this test case
        all_preds: all available predicates
        timeout_s: sample until this timeout
        
    Returns:
        summaries with quality, performance statistics
    """
    return cp.base.gen_rl(
        timeout_s=timeout_s, connection=connection, 
        all_preds=all_preds, **test_case)


def log_line(outfile, b_id, t_id, nr_facts, 
             nr_preds, m_id, sums, p_stats):
    """ Writes one line to log file.
    
    Args:
        outfile: pointer to output file
        b_id: batch ID
        t_id: test case ID
        nr_facts: summaries use that many facts
        nr_preds: number of predicates per fact
        m_id: method identifier
        sums: maps summaries to rewards
        p_stats: performance statistics
    """
    sorted_sums = sorted(sums.items(), key=lambda s: s[1])
    b_sum = sorted_sums[-1]
    w_sum = sorted_sums[0]
    
    e_time = p_stats['evaluation_time']
    time = p_stats['time']
    cache_hits = p_stats['cache_hits']
    cache_misses = p_stats['cache_misses']

    outfile.write(f'{b_id}\t{t_id}\t{nr_facts}\t{nr_preds}\t' \
                  f'{m_id}\t{b_sum[0]}\t{b_sum[1]}\t{w_sum[0]}\t{w_sum[1]}\t' \
                  f'{time}\t{e_time}\t{cache_hits}\t{cache_misses}\n')
    outfile.flush()


def main():
    
    parser = argparse.ArgumentParser(description='Run CP benchmark.')
    parser.add_argument('db', type=str, help='database name')
    parser.add_argument('user', type=str, help='database user')
    parser.add_argument('out', type=str, help='result file name')
    parser.add_argument('log', type=str, help='logging level')
    parser.add_argument('samples', type=int, help='Number RL samples')
    args = parser.parse_args()

    db = args.db
    user = args.user
    outpath = args.out
    nr_samples = args.samples
    
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')
    logging.basicConfig(level=log_level, filemode='w')
    
    with open(outpath, 'w') as file:
        file.write('scenario\ttestcase\tnrfacts\tnrpreds\t' \
                   'approach\tbest\tbquality\tworst\twquality\t'\
                   'time\tetime\tchits\tcmisses\n')
        with psycopg2.connect(database=db, user=user) as connection:
            connection.autocommit = True
            
            test_batches = cp.bench.generate_testcases()
            for b_id, b in enumerate(test_batches):
                for t_id, t in enumerate(b):
                    for nr_facts in [1, 2, 3]:
                        for nr_preds in [1, 2, 3]:
                    
                            print(f'Next: B{b_id}/T{t_id}; ' \
                                  f'{nr_facts}F, {nr_preds}P')
                            
                            all_preds = cp.pred.all_preds(
                                connection, t['table'], 
                                t['dim_cols'], t['cmp_pred'])
                            t['nr_facts'] = nr_facts
                            t['nr_preds'] = nr_preds
                            
                            for c_type in ['dynamic', 'cube', 'empty']:
                                sums, p_stats = run_rl(
                                    connection, t, all_preds, 
                                    nr_samples, c_type, 20)
                                log_line(
                                    file, b_id, t_id, nr_facts, nr_preds, 
                                    'rl' + c_type, sums, p_stats)
                                # timeout_s = p_stats['time']
                                #
                            # sums, p_stats = run_random(
                                # connection, t, all_preds, 1, float('inf'))
                            # log_line(
                                # file, b_id, t_id, nr_facts,
                                # nr_preds, 'rand1', sums, p_stats)
                                #
                            # sums, p_stats = run_random(
                                # connection, t, all_preds, 
                                # float('inf'), timeout_s)
                            # log_line(
                                # file, b_id, t_id, nr_facts,
                                # nr_preds, 'rand', sums, p_stats)
                                #
                            # sums, p_stats = run_gen(
                                # connection, t, all_preds, timeout_s)
                            # log_line(
                                # file, b_id, t_id, nr_facts,
                                # nr_preds, 'gen', sums, p_stats)

if __name__ == '__main__':
    main()