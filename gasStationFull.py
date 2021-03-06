import time
import sys
import json
import math
import traceback
import os
import random
import pandas as pd
import numpy as np
from web3 import Web3, HTTPProvider
from sqlalchemy import create_engine, Column, Integer, String, DECIMAL, BigInteger, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from egs import *
from per_block_analysis import *
from report_generator import *

web3 = Web3(HTTPProvider('http://localhost:8545'))
engine = create_engine(
    'mysql+mysqlconnector://ethgas:station@127.0.0.1:3306/tx', echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

   

def init_dfs():
    """load data from mysql"""
    blockdata = pd.read_sql('SELECT * from blockdata2 order by id desc limit 2000', con=engine)
    blockdata = blockdata.drop('id', axis=1)
    postedtx = pd.read_sql('SELECT * from postedtx2 order by id desc limit 100000', con=engine)
    minedtx = pd.read_sql('SELECT * from minedtx2 order by id desc limit 100000', con=engine)
    minedtx.set_index('index', drop=True, inplace=True)
    alltx = pd.read_sql('SELECT * from minedtx2 order by id desc limit 100000', con=engine)
    alltx.set_index('index', drop=True, inplace=True)
    alltx = postedtx[['index', 'expectedTime', 'expectedWait', 'mined_probability', 'highgas2', 'from_address', 'gas_offered', 'gas_price', 'hashpower_accepting', 'num_from', 'num_to', 'ico', 'dump', 'high_gas_offered', 'pct_limit', 'round_gp_10gwei', 'time_posted', 'block_posted', 'to_address', 'tx_atabove', 'wait_blocks', 'chained', 'nonce']].join(minedtx[['block_mined', 'miner', 'time_mined', 'removed_block']], on='index', how='left')
    alltx.set_index('index', drop=True, inplace=True)
    return(blockdata, alltx)

def prune_data(blockdata, alltx, txpool, block):
    """keep dataframes and databases from getting too big"""
    stmt = text("DELETE FROM postedtx2 WHERE block_posted <= :block")
    stmt2 = text("DELETE FROM minedtx2 WHERE block_mined <= :block")
    deleteBlock_sql = block - 3500
    deleteBlock_mined = block - 1700
    deleteBlock_posted = block - 5500
    engine.execute(stmt, block=deleteBlock_sql)
    engine.execute(stmt2, block=deleteBlock_sql)
    alltx = alltx.loc[(alltx['block_posted'] > deleteBlock_posted) | (alltx['block_mined'] > deleteBlock_mined)]
    blockdata = blockdata.loc[blockdata['block_number'] > deleteBlock_posted]
    txpool = txpool.loc[txpool['block'] > (block-10)]
    return (blockdata, alltx, txpool)

def write_to_sql(alltx, block_sumdf, mined_blockdf, block):
    """write data to mysql for analysis"""
    post = alltx[alltx.index.isin(mined_blockdf.index)]
    post.to_sql(con=engine, name='minedtx2', if_exists='append', index=True)
    print ('num mined = ' + str(len(post)))
    post2 = alltx.loc[alltx['block_posted'] == (block)]
    post2.to_sql(con=engine, name='postedtx2', if_exists='append', index=True)
    print ('num posted = ' + str(len(post2)))
    block_sumdf.to_sql(con=engine, name='blockdata2', if_exists='append', index=False)

def write_report(report, top_miners, price_wait, miner_txdata, gasguzz, lowprice):
    """write json data"""
    parentdir = os.path.dirname(os.getcwd())
    top_minersout = top_miners.to_json(orient='records')
    minerout = miner_txdata.to_json(orient='records')
    gasguzzout = gasguzz.to_json(orient='records')
    lowpriceout = lowprice.to_json(orient='records')
    price_waitout = price_wait.to_json(orient='records')
    filepath_report = parentdir + '/json/txDataLast10k.json'
    filepath_tminers = parentdir + '/json/topMiners.json'
    filepath_pwait = parentdir + '/json/priceWait.json'
    filepath_minerout = parentdir + '/json/miners.json'
    filepath_gasguzzout = parentdir + '/json/gasguzz.json'
    filepath_lowpriceout = parentdir + '/json/validated.json'

    try:
        with open(filepath_report, 'w') as outfile:
            json.dump(report, outfile, allow_nan=False)
        with open(filepath_tminers, 'w') as outfile:
            outfile.write(top_minersout)
        with open(filepath_pwait, 'w') as outfile:
            outfile.write(price_waitout)
        with open(filepath_minerout, 'w') as outfile:
            outfile.write(minerout)
        with open(filepath_gasguzzout, 'w') as outfile:
            outfile.write(gasguzzout)
        with open(filepath_lowpriceout, 'w') as outfile:
            outfile.write(lowpriceout)

    except Exception as e:
        print(e)

def write_to_json(gprecs, prediction_table=pd.DataFrame()):
    """write json data"""
    try:
        parentdir = os.path.dirname(os.getcwd())
        if not prediction_table.empty:
            prediction_table['gasprice'] = prediction_table['gasprice']/10
            prediction_tableout = prediction_table.to_json(orient='records')
            filepath_prediction_table = parentdir + '/json/predictTable.json'
            with open(filepath_prediction_table, 'w') as outfile:
                outfile.write(prediction_tableout)

        filepath_gprecs = parentdir + '/json/ethgasAPI.json'
        with open(filepath_gprecs, 'w') as outfile:
            json.dump(gprecs, outfile)

        

    
    except Exception as e:
        print(e)
    
def master_control(report_option):
    (blockdata, alltx) = init_dfs()
    txpool = pd.DataFrame()
    snapstore = pd.DataFrame()
    print ('blocks '+ str(len(blockdata)))
    print ('txcount '+ str(len(alltx)))
    timer = Timers(web3.eth.blockNumber)  
    start_time = time.time()
    first_cycle = True
    analyzed = 0

    
    def append_new_tx(clean_tx):
        nonlocal alltx
        if not clean_tx.hash in alltx.index:
            alltx = alltx.append(clean_tx.to_dataframe(), ignore_index = False)
    


    def update_dataframes(block):
        nonlocal alltx
        nonlocal txpool
        nonlocal blockdata
        nonlocal timer
        got_txpool = 1

        print('updating dataframes at block '+ str(block))
        try:
            #get minedtransactions and blockdata from previous block
            mined_block_num = block-3
            (mined_blockdf, block_obj) = process_block_transactions(mined_block_num)

            #add mined data to tx dataframe 
            mined_blockdf_seen = mined_blockdf[mined_blockdf.index.isin(alltx.index)]
            print('num mined in ' + str(mined_block_num)+ ' = ' + str(len(mined_blockdf)))
            print('num seen in ' + str(mined_block_num)+ ' = ' + str(len(mined_blockdf_seen)))
            alltx = alltx.combine_first(mined_blockdf)
           
            #process block data
            block_sumdf = process_block_data(mined_blockdf, block_obj)

            #add block data to block dataframe 
            blockdata = blockdata.append(block_sumdf, ignore_index = True)

            #get hashpower table, block interval time, gaslimit, speed from last 200 blocks
            (hashpower, block_time, gaslimit, speed) = analyze_last200blocks(block, blockdata)
            hpower2 = analyze_last100blocks(block, alltx)

            submitted_30mago = alltx.loc[(alltx['block_posted'] < (block-50)) & (alltx['block_posted'] > (block-120)) & (alltx['chained']==0) & (alltx['gas_offered'] < 500000)].copy()
            print("# of tx submitted ~ an hour ago: " + str((len(submitted_30mago))))

            submitted_5mago = alltx.loc[(alltx['block_posted'] < (block-8)) & (alltx['block_posted'] > (block-49)) & (alltx['chained']==0) & (alltx['gas_offered'] < 500000)].copy()
            print("# of tx submitted ~ 5m ago: " + str((len(submitted_5mago))))

            if len(submitted_30mago > 50):
                submitted_30mago = make_recent_blockdf(submitted_30mago, current_txpool, alltx)
            else:
                submitted_30mago = pd.DataFrame()

            if len(submitted_5mago > 50):
                submitted_5mago = make_recent_blockdf(submitted_5mago, current_txpool, alltx)
            else:
                submitted_5mago = pd.DataFrame()

            #make txpool block data
            txpool_block = make_txpool_block(block, txpool, alltx)
            
            if not txpool_block.empty: 
                #new dfs grouped by gasprice and nonce
                txpool_by_gp = txpool_block[['gas_price', 'round_gp_10gwei']].groupby('round_gp_10gwei').agg({'gas_price':'count'})
                txpool_block_nonce = txpool_block[['from_address', 'nonce']].groupby('from_address').agg({'nonce':'min'})
                txpool_block = analyze_nonce(txpool_block, txpool_block_nonce)
            else:
                txpool_by_gp = pd.DataFrame()
                txpool_block_nonce = pd.DataFrame()
                txpool_block = alltx.loc[alltx['block_posted']==block]
                got_txpool = 0

            #make prediction table and create lookups to speed txpool analysis
            (predictiondf, txatabove_lookup, gp_lookup, gp_lookup2) = make_predcitiontable(hashpower, hpower2, block_time, txpool_by_gp, submitted_5mago, submitted_30mago)

            #with pd.option_context('display.max_rows', None,):
                #print(predictiondf)

            #make the gas price recommendations
            (gprecs, timer.gp_avg_store, timer.gp_safelow_store) = get_gasprice_recs (predictiondf, block_time, block, speed, timer.gp_avg_store, timer.gp_safelow_store, timer.minlow, submitted_5mago, submitted_30mago)

            #create the txpool block data
            #first, add txs submitted if empty

            try:
                if txpool_block.notnull:
                    analyzed_block = analyze_txpool(block, txpool_block, hashpower, hpower2, block_time, gaslimit, txatabove_lookup, gp_lookup, gp_lookup2, gprecs)
                    #update alltx 
                    alltx = alltx.combine_first(analyzed_block)
            except:
                pass
                
            #with pd.option_context('display.max_columns', None,):
                #print(analyzed_block)

            #make summary report every x blocks
            #this is only run if generating reports for website
            if report_option == '-r':
                if timer.check_reportblock(block):
                    last1500t = alltx[alltx['block_mined'] > (block-1500)].copy()
                    print('txs '+ str(len(last1500t)))
                    last1500b = blockdata[blockdata['block_number'] > (block-1500)].copy()
                    print('blocks ' +  str(len(last1500b)))
                    report = SummaryReport(last1500t, last1500b, block)
                    write_report(report.post, report.top_miners, report.price_wait, report.miner_txdata, report.gasguzz, report.lowprice)
                    timer.minlow = report.minlow


            #every block, write gprecs, predictions, txpool by gasprice

            if got_txpool:
                write_to_json(gprecs, predictiondf)
            else:
                write_to_json(gprecs)

            write_to_sql(alltx, block_sumdf, mined_blockdf, block)

            #keep from getting too large
            (blockdata, alltx, txpool) = prune_data(blockdata, alltx, txpool, block)
            return True

        except: 
            print(traceback.format_exc())    

    
    while True:
        try:
            block = web3.eth.blockNumber
            if first_cycle == True and block != analyzed:
                analyzed = block
                tx_filter = web3.eth.filter('pending')
                #get list of txhashes from txpool
                print("getting txpool hashes at block " +str(block) +" ...") 
                current_txpool = get_txhases_from_txpool(block)
                #add txhashes to txpool dataframe
                print("done. length = " +str(len(current_txpool)))
                txpool = txpool.append(current_txpool, ignore_index = False)
        except:
            pass
             
        try:
            new_tx_list = web3.eth.getFilterChanges(tx_filter.filter_id)
        except:
            tx_filter = web3.eth.filter('pending')
            new_tx_list = web3.eth.getFilterChanges(tx_filter.filter_id)
        
        timestamp = time.time()

        #this can be adjusted depending on how fast your server is
        if timer.process_block <= (block-5) and len(new_tx_list) > 10:
            print("sampling 10 from " + str(len(new_tx_list)) + " new tx")
            new_tx_list = random.sample(new_tx_list, 10)
        elif timer.process_block == (block-4) and len(new_tx_list) > 25:
            print("sampling 25 from " + str(len(new_tx_list)) + " new tx")
            new_tx_list = random.sample(new_tx_list, 25)
        elif timer.process_block == (block-3) and len(new_tx_list) > 50:
            print("sampling 50 from " + str(len(new_tx_list)) + " new tx")
            new_tx_list = random.sample(new_tx_list, 50)
        elif timer.process_block == (block-2) and len(new_tx_list) > 100:
            print("sampling 100 from " + str(len(new_tx_list)) + " new tx")
            new_tx_list = random.sample(new_tx_list, 100)
        elif timer.process_block == (block-1) and len(new_tx_list) > 200:
            print("sampling 200 from " + str(len(new_tx_list)) + " new tx")
            new_tx_list = random.sample(new_tx_list, 200)
       
        for new_tx in new_tx_list:    
            try:        
                tx_obj = web3.eth.getTransaction(new_tx)
                clean_tx = CleanTx(tx_obj, block, timestamp)
                append_new_tx(clean_tx)
            except Exception as e:
                pass

        first_cycle = False
        
        if (timer.process_block < block):
                try:
                    test_filter = web3.eth.uninstallFilter(tx_filter.filter_id)
                except:
                    pass
                print('current block ' +str(block))
                print ('processing block ' + str(timer.process_block))
                updated = update_dataframes(timer.process_block)
                print ('finished ' + str(timer.process_block) + "\n")
                timer.process_block = timer.process_block + 1
                first_cycle = True
        
        if (timer.process_block < (block - 8)):
                print("skipping ahead \n")
                timer.process_block = (block-1)
              
    
            
if len(sys.argv) > 1:            
    report_option = sys.argv[1] # '-r' = make website report
else:
    report_option = False

master_control(report_option)
