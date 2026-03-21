from contextlib import asynccontextmanager
import asyncio
import logging
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List
from dotenv import dotenv_values, set_key, find_dotenv
from service import strategy_service
from utils import parse_symbol_expiry
from logger import LoggerSetup

logger = LoggerSetup.get_logger("StratAPI")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await strategy_service.start()
    yield
    # Shutdown
    await strategy_service.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/restAPI/System/Health")
async def system_health():
    """Detailed health status for GUI indicators."""
    is_ready = getattr(strategy_service, 'is_ready', False)
    is_running = getattr(strategy_service, 'running', False)
    
    if is_ready:
        status = "ready"
        details = "Strategy engine operational and connected to IB_Core."
    elif is_running:
        status = "connecting"
        details = "Waiting for IB_Core connection or initializing..."
    else:
        status = "error"
        details = "Strategy engine is stopped or encountered a critical error."
        
    return {
        "status": status,
        "is_ready": is_ready,
        "details": details
    }

# --- Configuration Endpoints ---

@app.get("/restAPI/config")
async def get_config():
    """Returns the current .env parameters."""
    env_path = find_dotenv()
    if not env_path:
        raise HTTPException(status_code=404, detail=".env file not found")
    return dotenv_values(env_path)

@app.post("/restAPI/config")
async def update_config(payload: Dict[str, Any] = Body(...)):
    """Updates .env parameters without destroying comments/formatting."""
    env_path = find_dotenv()
    if not env_path:
        raise HTTPException(status_code=404, detail=".env file not found")
    
    for key, value in payload.items():
        set_key(env_path, key, str(value))
        
    return {"status": "success", "updated_keys": list(payload.keys())}

@app.get("/restAPI/strategies")
async def get_strategies():
    """
    Returns active strategies and their current state/configuration.
    """
    results = []
    for strat in strategy_service.strategies:
        # Get runtime state (composite keys)
        full_state = getattr(strat, 'runtime_state', {})
        
        # If strategy has multiple contracts (PentagramaRu style)
        config_contracts = getattr(strat, 'config', {}).get('contracts', [])
        
        if config_contracts:
            for contract in config_contracts:
                symbol = contract.get('symbol')
                if not symbol: continue
                
                is_enabled = contract.get('enabled', True)
                
                # Filter state for this symbol
                # Keys are "SYMBOL::LID"
                contract_state = {}
                prefix = f"{symbol}::"
                for cid, data in full_state.items():
                    if cid.startswith(prefix):
                        contract_state[cid] = data

                # Get PnL State
                tracker = strat.trackers.get(symbol)
                pnl_state = tracker.get_state() if tracker else {}

                strat_data = {
                    "StratName": strat.name, 
                    "symbol": symbol,
                    "config": contract, 
                    "enabled": is_enabled,
                    "performance": pnl_state,
                    "runtime_state": contract_state
                }
                results.append(strat_data)
        else:
            # Fallback for single-contract legacy strategies
            # Fallback for strategies without multiple contracts structure
            config = getattr(strat, 'config', {})
            symbol = config.get('contracts', [{}])[0].get('symbol', 'UNKNOWN')
            
            tracker = getattr(strat, 'trackers', {}).get(symbol)
            pnl_state = tracker.get_state() if tracker else {}
            
            strat_data = {
                "StratName": strat.name,
                "symbol": symbol,
                "config": config,
                "enabled": strat.enabled,
                "performance": pnl_state
            }
            results.append(strat_data)
        
    return results

@app.post("/restAPI/strategies/reload")
async def reload_strategies():
    """
    Reloads all strategies from the configuration file.
    """
    try:
        await strategy_service.reload()
        return {"status": "success", "message": "Strategies reloaded"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ToggleRequest(BaseModel):
    enabled: bool

@app.post("/restAPI/strategies/{strat_name}/{symbol}/toggle")
async def toggle_strategy(strat_name: str, symbol: str, req: ToggleRequest):
    """
    Toggles the enabled status of a specific contract within a strategy.
    Updates the in-memory state and persists to strategies.yaml.
    """
    # 1. Persist to YAML and update config memory
    success = strategy_service.config.toggle_strategy_contract(strat_name, symbol, req.enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{strat_name}' or symbol '{symbol}' not found in configuration.")

    # 2. Update the active Strategy instance's memory
    updated_memory = False
    for strat in strategy_service.strategies:
        if strat.name == strat_name:
            # Check if this strategy has the multiple contracts structure
            if hasattr(strat, 'config') and 'contracts' in strat.config:
                for contract in strat.config['contracts']:
                    if contract.get('symbol') == symbol:
                        contract['enabled'] = req.enabled
                        updated_memory = True
                        break
            
            if updated_memory and hasattr(strat, 'on_contract_enabled_changed'):
                asyncio.create_task(strat.on_contract_enabled_changed(symbol, req.enabled))

    if not updated_memory:
        raise HTTPException(status_code=404, detail="Active strategy instance not found to apply toggle.")

    logger.info(f"[API] Strategy {strat_name} | {symbol} Enabled/Disabled status changed to: {req.enabled}")
    return {"status": "success", "strat_name": strat_name, "symbol": symbol, "enabled": req.enabled}

class ToggleRecreateRequest(BaseModel):
    auto_recreate: bool

@app.post("/restAPI/strategies/{strat_name}/{symbol}/toggle_recreate")
async def toggle_strategy_recreate(strat_name: str, symbol: str, req: ToggleRecreateRequest):
    success = strategy_service.config.toggle_strategy_auto_recreate(strat_name, symbol, req.auto_recreate)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{strat_name}' or symbol '{symbol}' not found in configuration.")

    updated_memory = False
    for strat in strategy_service.strategies:
        if strat.name == strat_name:
            if hasattr(strat, 'config') and 'contracts' in strat.config:
                for contract in strat.config['contracts']:
                    if contract.get('symbol') == symbol:
                        contract['auto_recreate'] = req.auto_recreate
                        updated_memory = True
                        break
            
            if updated_memory and hasattr(strat, 'on_auto_recreate_changed'):
                # Notify strategy asynchronously
                asyncio.create_task(strat.on_auto_recreate_changed(symbol, req.auto_recreate))

    if not updated_memory:
        raise HTTPException(status_code=404, detail="Active strategy instance not found to apply toggle.")

    logger.info(f"[API] Strategy {strat_name} | {symbol} Auto-Recreate status changed to: {req.auto_recreate}")
    return {"status": "success", "strat_name": strat_name, "symbol": symbol, "auto_recreate": req.auto_recreate}

class ToggleAutoFixRequest(BaseModel):
    auto_fix: bool

@app.post("/restAPI/strategies/{strat_name}/{symbol}/toggle_auto_fix")
async def toggle_strategy_auto_fix(strat_name: str, symbol: str, req: ToggleAutoFixRequest):
    success = strategy_service.config.toggle_strategy_auto_fix(strat_name, symbol, req.auto_fix)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{strat_name}' or symbol '{symbol}' not found in configuration.")

    updated_memory = False
    for strat in strategy_service.strategies:
        if strat.name == strat_name:
            if hasattr(strat, 'config') and 'contracts' in strat.config:
                for contract in strat.config['contracts']:
                    if contract.get('symbol') == symbol:
                        contract['auto_fix'] = req.auto_fix
                        updated_memory = True
                        break
            
            if updated_memory and hasattr(strat, 'on_auto_fix_changed'):
                # Notify strategy asynchronously
                asyncio.create_task(strat.on_auto_fix_changed(symbol, req.auto_fix))

    if not updated_memory:
        raise HTTPException(status_code=404, detail="Active strategy instance not found to apply toggle.")

    logger.info(f"[API] Strategy {strat_name} | {symbol} Auto-Fix status changed to: {req.auto_fix}")
    return {"status": "success", "strat_name": strat_name, "symbol": symbol, "auto_fix": req.auto_fix}

@app.post("/restAPI/strategies/{strat_name}/{symbol}/levels/{lid}/fix")
async def manual_fix_level(strat_name: str, symbol: str, lid: str):
    """
    Manually triggers a fix for a specific level.
    """
    for strat in strategy_service.strategies:
        if strat.name == strat_name:
            if hasattr(strat, 'manual_fix_level'):
                try:
                    # Let the strategy handle the recovery logic
                    asyncio.create_task(strat.manual_fix_level(symbol, lid))
                    return {"status": "success", "message": f"Manual fix triggered for {symbol} level {lid}"}
                except Exception as e:
                    logger.error(f"Error triggering manual fix: {e}")
                    raise HTTPException(status_code=500, detail=str(e))
    
    raise HTTPException(status_code=404, detail="Strategy or capability not found.")

class AssumeExecutedRequest(BaseModel):
    order_type: str # 'PARENT', 'TP', 'SL'
    
@app.post("/restAPI/strategies/{strat_name}/{symbol}/levels/{lid}/assume_executed")
async def assume_executed_level(strat_name: str, symbol: str, lid: str, req: AssumeExecutedRequest):
    """
    Manually triggers 'Assume Executed' for a specific order in a level.
    """
    for strat in strategy_service.strategies:
        if strat.name == strat_name:
            if hasattr(strat, 'assume_order_executed'):
                try:
                    await asyncio.create_task(strat.assume_order_executed(symbol, lid, req.order_type))
                    return {"status": "success", "message": f"Assumed {req.order_type} executed for {symbol} level {lid}"}
                except ValueError as ve:
                    raise HTTPException(status_code=400, detail=str(ve))
                except Exception as e:
                    logger.error(f"Error assuming execution: {e}")
                    raise HTTPException(status_code=500, detail=str(e))
    
    raise HTTPException(status_code=404, detail="Strategy or capability not found.")
