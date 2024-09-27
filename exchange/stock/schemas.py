from enum import Enum
from typing import Literal, List, Dict, Any, Optional
from pydantic import BaseModel, Field

korea_stocks = ("KRX")
us_stocks = ("NASDAQ", "NYSE", "AMEX")


class BaseUrls(str, Enum):
    base_url = "https://openapi.koreainvestment.com:9443"  
    paper_base_url = "https://openapivts.koreainvestment.com:29443"  


class BaseHeaders(BaseModel):
    authorization: str  
    appkey: str         
    appsecret: str      
    custtype: str = "P" 


class Endpoints(str, Enum):
    korea_order_base = "/uapi/domestic-stock/v1"
    korea_order = f"{korea_order_base}/trading/order-cash"  
    korea_order_buyable = f"{korea_order_base}/trading/inquire-psbl-order"  
    korea_balance = f"{korea_order_base}/trading/inquire-balance"  
    
    usa_order_base = "/uapi/overseas-stock/v1"
    usa_order = f"{usa_order_base}/trading/order"  
    usa_order_buyable = f"{usa_order_base}/trading/inquire-psamount"  
    usa_current_price = f"/uapi/overseas-price/v1/quotations/price"  
    usa_balance = f"{usa_order_base}/trading/inquire-balance"  

    korea_ticker = "/uapi/domestic-stock/v1/quotations/inquire-price"
    usa_ticker = "/uapi/overseas-price/v1/quotations/price"


class TransactionId(str, Enum):
    korea_buy = "TTTC0802U"  
    korea_sell = "TTTC0801U"  
    korea_balance = "TTTC8434R"  
    korea_paper_balance = "VTTC8434R"  

    korea_paper_buy = "VTTC0802U"  
    korea_paper_sell = "VTTC0801U"  
    korea_paper_cancel = "VTTC0803U"  

    usa_buy = "JTTT1002U"  
    usa_sell = "JTTT1006U"  
    usa_balance = "TTTS3012R"  
    usa_balance_mock = "VTTS3012R" 
 
    usa_paper_buy = "VTTT1002U"  
    usa_paper_sell = "VTTT1001U"  

    korea_ticker = "FHKST01010100"  
    usa_ticker = "HHDFS00000300"    


class KoreaTickerQuery(BaseModel):
    FID_COND_MRKT_DIV_CODE: str = "J"  
    FID_INPUT_ISCD: str                  


class UsaTickerQuery(BaseModel):
    AUTH: str = ""                        
    EXCD: Literal["NYS", "NAS", "AMS"]    
    SYMB: str                             


class ExchangeCode(str, Enum):
    NYSE = "NYSE"    
    NASDAQ = "NASD"  
    AMEX = "AMEX"    


class QueryExchangeCode(str, Enum):
    NYSE = "NYS"
    NASDAQ = "NAS"
    AMEX = "AMS"


class KoreaOrderType(str, Enum):
    market = "01"  
    limit = "00"   


class UsaOrderType(str, Enum):
    limit = "00"  


class OrderSide(str, Enum):
    buy = "buy"    
    sell = "sell"  


class TokenInfo(BaseModel):
    access_token: str                     
    access_token_token_expired: str       


class KoreaTickerHeaders(BaseHeaders):
    tr_id: str = TransactionId.korea_ticker.value  


class UsaTickerHeaders(BaseHeaders):
    tr_id: str = TransactionId.usa_ticker.value    


class KoreaBuyOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.korea_buy.value      


class KoreaSellOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.korea_sell.value     


class KoreaPaperBuyOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.korea_paper_buy.value  


class KoreaPaperSellOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.korea_paper_sell.value  


class UsaBuyOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.usa_buy.value


class UsaSellOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.usa_sell.value


class UsaPaperBuyOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.usa_paper_buy.value


class UsaPaperSellOrderHeaders(BaseHeaders):
    tr_id: str = TransactionId.usa_paper_sell.value


class AccountInfo(BaseModel):
    CANO: str          
    ACNT_PRDT_CD: str  


class OrderBody(BaseModel):
    PDNO: str      
    ORD_QTY: str   


class KoreaOrderBody(OrderBody):
    ORD_DVSN: Literal[KoreaOrderType.market, KoreaOrderType.limit]  
    ORD_UNPR: str  


class KoreaMarketOrderBody(KoreaOrderBody):
    ORD_DVSN: str = KoreaOrderType.market.value  
    ORD_UNPR: str = "0"                           


class UsaOrderBody(OrderBody):
    ORD_DVSN: str = UsaOrderType.limit.value     
    OVRS_ORD_UNPR: str                         
    OVRS_EXCG_CD: Literal[ExchangeCode.NYSE, ExchangeCode.NASDAQ, ExchangeCode.AMEX]  
    ORD_SVR_DVSN_CD: str = "0"                    


class KoreaStockBalanceRequest(BaseModel):
    CANO: str                       
    ACNT_PRDT_CD: str               
    AFHR_FLPR_YN: Literal['N', 'Y'] 
    OFL_YN: str = ""                
    INQR_DVSN: Literal['01', '02']  
    UNPR_DVSN: Literal['01']        
    FUND_STTL_ICLD_YN: Literal['N', 'Y'] 
    FNCG_AMT_AUTO_RDPT_YN: Literal['N', 'Y'] 
    PRCS_DVSN: Literal['00', '01']  
    CTX_AREA_FK100: str = ""        
    CTX_AREA_NK100: str = ""        


class KoreaStockBalanceItem(BaseModel):
    pdno: str                      
    prdt_name: str                 
    hldg_qty: int                  
    ord_psbl_qty: int              
    prpr: float                    
    evlu_amt: int                  
   
    class Config:
        extra = "ignore"  


class KoreaStockBalanceResponse(BaseModel):
    output1: List[KoreaStockBalanceItem]        
    rt_cd: str                                 
    msg_cd: str                                
    msg1: str                                  

    class Config:
        extra = "ignore"  


class UsaStockBalanceRequest(BaseModel):
    CANO: str                       
    ACNT_PRDT_CD: str               
    OVRS_EXCG_CD: Literal["NYS", "NAS", "AMS", "NASD"]  
    TR_CRCY_CD: Literal["USD"]      
    CTX_AREA_FK200: str = ""        
    CTX_AREA_NK200: str = ""        


class UsaStockBalanceItem(BaseModel):
    cano: Optional[str] = Field(None, alias="cano")                           
    acnt_prdt_cd: Optional[str] = Field(None, alias="acnt_prdt_cd")           
    prdt_type_cd: Optional[str] = Field(None, alias="prdt_type_cd")           
    ovrs_pdno: Optional[str] = Field(None, alias="ovrs_pdno")                 
    ovrs_item_name: Optional[str] = Field(None, alias="ovrs_item_name")       
    frcr_evlu_pfls_amt: Optional[str] = Field(None, alias="frcr_evlu_pfls_amt")  
    evlu_pfls_rt: Optional[str] = Field(None, alias="evlu_pfls_rt")           
    pchs_avg_pric: Optional[str] = Field(None, alias="pchs_avg_pric")         
    ovrs_cblc_qty: Optional[str] = Field(None, alias="ovrs_cblc_qty")         
    ord_psbl_qty: Optional[str] = Field(None, alias="ord_psbl_qty")           
    frcr_pchs_amt1: Optional[str] = Field(None, alias="frcr_pchs_amt1")       
    ovrs_stck_evlu_amt: Optional[str] = Field(None, alias="ovrs_stck_evlu_amt")  
    now_pric2: Optional[str] = Field(None, alias="now_pric2")                 
    tr_crcy_cd: Optional[str] = Field(None, alias="tr_crcy_cd")               
    ovrs_excg_cd: Optional[str] = Field(None, alias="ovrs_excg_cd")           
    loan_type_cd: Optional[str] = Field(None, alias="loan_type_cd")           
    loan_dt: Optional[str] = Field(None, alias="loan_dt")                     
    expd_dt: Optional[str] = Field(None, alias="expd_dt")                     

    class Config:
        allow_population_by_field_name = True
        extra = "ignore"  


class UsaStockBalanceSummary(BaseModel):
    frcr_pchs_amt1: Optional[str] = Field(None, alias="frcr_pchs_amt1")         
    ovrs_rlzt_pfls_amt: Optional[str] = Field(None, alias="ovrs_rlzt_pfls_amt") 
    ovrs_tot_pfls: Optional[str] = Field(None, alias="ovrs_tot_pfls")           
    rlzt_erng_rt: Optional[str] = Field(None, alias="rlzt_erng_rt")             
    tot_evlu_pfls_amt: Optional[str] = Field(None, alias="tot_evlu_pfls_amt")   
    tot_pftrt: Optional[str] = Field(None, alias="tot_pftrt")                   
    frcr_buy_amt_smtl1: Optional[str] = Field(None, alias="frcr_buy_amt_smtl1") 
    ovrs_rlzt_pfls_amt2: Optional[str] = Field(None, alias="ovrs_rlzt_pfls_amt2") 
    frcr_buy_amt_smtl2: Optional[str] = Field(None, alias="frcr_buy_amt_smtl2") 

    class Config:
        allow_population_by_field_name = True
        extra = "ignore"  


class UsaStockBalanceResponse(BaseModel):
    output1: List[UsaStockBalanceItem]         
    output2: Optional[UsaStockBalanceSummary]  
    rt_cd: str                                 
    msg_cd: str                                
    msg1: str                                  

    class Config:
        allow_population_by_field_name = True
        extra = "ignore"  
