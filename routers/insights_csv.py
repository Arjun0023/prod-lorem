from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from typing import List, Optional
import pandas as pd
import numpy as np
import json
import io
from utility.utils import NpEncoder, get_genai_client, sanitize_for_json
from prompt.insights_prompt import INSIGHTS_PROMPT
from prompt.deeper_insights_chat import DEEPER_INSIGHTS_CHAT_PROMPT  # Import the chat prompt

router = APIRouter()

# Enhanced in-memory session storage to include DataFrames
chat_sessions = {}

@router.post("/deeper-insights-chat")
async def deeper_insights_chat(
    question: str = Form(...),
    language: Optional[str] = Form("English"),
    sessionId: Optional[str] = Form(None),
    isFollowUp: Optional[str] = Form("false")
):
    """
    Handle follow-up chat questions with full context including DataFrames and previous responses.
    """
    try:
        print(f"Processing chat question: {question}")
        print(f"Session ID: {sessionId}")
        print(f"Language: {language}")
        
        # Validate required fields  
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")
        
        # Check if session exists and has context
        if not sessionId or sessionId not in chat_sessions:
            return {
                "insights": f"I don't have access to your previous data analysis. Please upload your files again to get insights based on your data. I can provide general information, but for specific data analysis, I need the context from your uploaded files.",
                "question": question,
                "language": language or "English",
                "isFollowUp": True,
                "sessionId": sessionId,
                "success": True,
                "message": "No previous context available - general response provided"
            }
        
        session_data = chat_sessions[sessionId]
        
        # Get the latest data context (from the most recent CSV upload)
        latest_data_context = None
        dataframes_context = None
        previous_responses = []
        
        for entry in session_data:
            if 'dataframes_context' in entry:
                latest_data_context = entry
                dataframes_context = entry['dataframes_context']
            if 'response' in entry:
                previous_responses.append({
                    'question': entry.get('question', ''),
                    'response': entry.get('response', ''),
                    'timestamp': entry.get('timestamp', '')
                })
        
        if not dataframes_context:
            return {
                "insights": f"I don't have access to your uploaded data in this session. Please upload your CSV/Excel files again to continue the analysis.",
                "question": question,
                "language": language or "English",
                "isFollowUp": True,
                "sessionId": sessionId,
                "success": True,
                "message": "No data context available in session"
            }
        
        # Get Gemini client
        model = get_genai_client()
        
        # Prepare the context for the chat prompt
        try:
            # Convert dataframes context to string representation
            context_str = json.dumps(dataframes_context, cls=NpEncoder, indent=2)
        except Exception as json_error:
            print(f"Error serializing context: {json_error}")
            # Fallback: create a simpler context
            simple_context = {
                file_key: {
                    'filename': info['filename'],
                    'shape': info['shape'],
                    'columns': info['columns'],
                    'total_rows': info['total_rows'],
                    'sample_data': info['sample_data'][:5] if 'sample_data' in info else []
                }
                for file_key, info in dataframes_context.items()
            }
            context_str = json.dumps(simple_context, indent=2)
        
        # Prepare previous conversation history
        conversation_history = ""
        if previous_responses:
            conversation_history = "\n".join([
                f"Previous Q: {resp['question']}\nPrevious A: {resp['response']}\n---"
                for resp in previous_responses[-3:]  # Last 3 exchanges to avoid too long context
            ])
        
        # Create the chat prompt using the imported prompt template
        chat_prompt = DEEPER_INSIGHTS_CHAT_PROMPT.format(
            current_question=question,
            language=language or "English",
            data_context=context_str,
            conversation_history=conversation_history
        )
        
        print("Calling Gemini model for chat response with full context...")
        
        # Call the Gemini model with the chat prompt
        response = model.generate_content(chat_prompt)
        print(f"Generated contextual chat response successfully")
        
        # Add this exchange to the session history
        chat_sessions[sessionId].append({
            "question": question,
            "response": response.text,
            "timestamp": str(pd.Timestamp.now()),
            "language": language,
            "interaction_type": "chat"
        })
        
        # Return the chat response
        return {
            "insights": response.text,
            "question": question,
            "language": language or "English",
            "isFollowUp": True,
            "sessionId": sessionId,
            "success": True,
            "message": "Chat response generated with full context",
            "context_available": True,
            "files_in_context": [info['filename'] for info in dataframes_context.values()] if dataframes_context else []
        }
        
    except HTTPException as he:
        print(f"HTTP Exception in chat: {he.detail}")
        raise he
    except Exception as e:
        print(f"Unexpected error in deeper_insights_chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during chat response: {str(e)}")


@router.post("/deeper-insights-csv")
async def deeper_insights_csv(
    files: List[UploadFile] = File(...),
    question: str = Form(...),
    language: Optional[str] = Form("English"),
    sessionId: Optional[str] = Form(None)
):
    try:
        # Validate that files are provided
        if not files:
            raise HTTPException(status_code=400, detail="No CSV files provided")
        
        # Validate required fields
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")
        
        print(f"Processing {len(files)} files with question: {question}")
        print(f"Session ID: {sessionId}")
        
        # Dictionary to store all DataFrames and their context
        dataframes = {}
        dataframes_raw = {}  # Store actual DataFrames for future reference
        
        # Process each uploaded file
        for file in files:
            print(f"Processing file: {file.filename}")
            
            # Validate file type
            if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid file type for {file.filename}. Only CSV and Excel files are allowed"
                )
            
            # Read file content
            file_content = await file.read()
            
            try:
                # Initialize variables for tracking
                encoding_used = "utf-8"
                df = None
                
                # Convert to pandas DataFrame based on file type
                if file.filename.lower().endswith('.csv'):
                    # Handle CSV with multiple encoding attempts
                    try:
                        # First try with BytesIO (binary mode)
                        df = pd.read_csv(io.BytesIO(file_content))
                        print(f"Successfully read {file.filename} with default encoding")
                    except UnicodeDecodeError:
                        print(f"UTF-8 failed for {file.filename}, trying other encodings...")
                        # Try with different encodings
                        encodings = ['latin-1', 'iso-8859-1', 'windows-1252', 'cp1252']
                        for encoding in encodings:
                            try:
                                df = pd.read_csv(io.BytesIO(file_content), encoding=encoding)
                                encoding_used = encoding
                                print(f"Successfully read {file.filename} with {encoding} encoding")
                                break
                            except Exception as enc_error:
                                print(f"Failed with {encoding}: {enc_error}")
                                continue
                        else:
                            raise HTTPException(
                                status_code=400, 
                                detail=f"Unable to decode CSV file {file.filename}. Please try saving it with UTF-8 encoding."
                            )
                    except Exception as csv_error:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"Error reading CSV file {file.filename}: {str(csv_error)}"
                        )
                        
                elif file.filename.lower().endswith(('.xlsx', '.xls')):
                    # Handle Excel files
                    try:
                        df = pd.read_excel(io.BytesIO(file_content))
                        print(f"Successfully read Excel file: {file.filename}")
                    except Exception as excel_error:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"Error reading Excel file {file.filename}: {str(excel_error)}"
                        )
                
                if df is None:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Failed to process file {file.filename}"
                    )
                
                # Clean column names (strip whitespace)
                df.columns = df.columns.str.strip()
                
                # Basic data cleaning
                df = df.dropna(how='all')  # Remove completely empty rows
                
                print(f"File {file.filename} processed: {df.shape[0]} rows, {df.shape[1]} columns")
                
                # Store DataFrame info for context
                file_key = file.filename.replace('.', '_').replace(' ', '_').replace('-', '_')
                
                # Store the actual DataFrame for future use
                dataframes_raw[file_key] = df.copy()
                
                # Get sample data with NaN handling
                sample_data = df.head(20).replace({np.nan: None})  # Increased sample size
                sample_data_dict = sanitize_for_json(sample_data.to_dict('records'))
                
                # Get summary statistics with proper NaN handling
                try:
                    summary_stats = df.describe(include='all').replace({np.nan: None}).to_dict()
                    summary_stats = sanitize_for_json(summary_stats)
                except Exception as stats_error:
                    print(f"Error generating summary stats for {file.filename}: {stats_error}")
                    summary_stats = {}
                
                # Get null counts
                null_counts = df.isnull().sum().to_dict()
                null_counts = {k: int(v) for k, v in null_counts.items()}  # Convert numpy int to Python int
                
                # Get unique value counts for categorical columns
                unique_counts = {}
                categorical_cols = df.select_dtypes(include=['object', 'category']).columns
                for col in categorical_cols[:10]:  # Limit to first 10 categorical columns
                    try:
                        unique_counts[col] = int(df[col].nunique())
                    except:
                        pass
                
                dataframes[file_key] = {
                    'filename': file.filename,
                    'shape': df.shape,
                    'columns': df.columns.tolist(),
                    'dtypes': {k: str(v) for k, v in df.dtypes.to_dict().items()},
                    'sample_data': sample_data_dict,
                    'summary_stats': summary_stats,
                    'null_counts': null_counts,
                    'unique_counts': unique_counts,
                    'total_rows': len(df),
                    'encoding_used': encoding_used
                }
                
                # If dataset is reasonably sized, include more data
                if len(df) <= 200:
                    full_data = df.replace({np.nan: None}).to_dict('records')
                    dataframes[file_key]['full_data'] = sanitize_for_json(full_data)
                elif len(df) <= 1000:
                    # For medium datasets, include first and last rows
                    sample_extended = pd.concat([df.head(50), df.tail(50)]).replace({np.nan: None})
                    dataframes[file_key]['extended_sample'] = sanitize_for_json(sample_extended.to_dict('records'))
                
            except HTTPException as he:
                raise he
            except Exception as e:
                print(f"Unexpected error processing {file.filename}: {str(e)}")
                raise HTTPException(
                    status_code=400, 
                    detail=f"Error processing file {file.filename}: {str(e)}"
                )
        
        print("All files processed successfully, generating insights...")
        
        # Convert dataframes info to string representation for the model
        try:
            context_str = json.dumps(dataframes, cls=NpEncoder, indent=2)
        except Exception as json_error:
            print(f"Error serializing context: {json_error}")
            # Fallback: create a simpler context
            simple_context = {
                file_key: {
                    'filename': info['filename'],
                    'shape': info['shape'],
                    'columns': info['columns'],
                    'total_rows': info['total_rows'],
                    'sample_data': info['sample_data'][:10]  # Only first 10 rows
                }
                for file_key, info in dataframes.items()
            }
            context_str = json.dumps(simple_context, indent=2)
        
        # Get Gemini client
        model = get_genai_client()
        
        # Create a combined prompt for deeper insights
        prompt = INSIGHTS_PROMPT.format(
            question=question,
            language=language or "English",
            context=context_str
        )
        
        print("Calling Gemini model for insights...")
        
        # Call the Gemini model with the insights prompt
        response = model.generate_content(prompt)
        print(f"Generated insights successfully")
        
        # Store comprehensive session data
        if sessionId:
            if sessionId not in chat_sessions:
                chat_sessions[sessionId] = []
            
            # Store comprehensive context for this session
            session_context = {
                "files_processed": [info['filename'] for info in dataframes.values()],
                "total_rows": sum(info['total_rows'] for info in dataframes.values()),
                "columns": [col for info in dataframes.values() for col in info['columns']],
                "question": question,
                "response": response.text,
                "timestamp": str(pd.Timestamp.now()),
                "language": language,
                "interaction_type": "initial_upload",
                "dataframes_context": dataframes,  # Store the full context
                "dataframes_raw": dataframes_raw  # Store actual DataFrames (note: this might cause memory issues in production)
            }
            
            chat_sessions[sessionId].append(session_context)
        
        # Prepare file processing summary
        files_processed = []
        for info in dataframes.values():
            files_processed.append({
                "filename": info['filename'],
                "rows": info['total_rows'],
                "columns": len(info['columns']),
                "encoding_used": info.get('encoding_used', 'utf-8')
            })
        
        # Return the insights along with file information
        return {
            "insights": response.text,
            "question": question,
            "language": language or "English",
            "files_processed": files_processed,
            "total_files": len(files),
            "sessionId": sessionId,
            "success": True,
            "context_stored": True
        }
        
    except HTTPException as he:
        print(f"HTTP Exception: {he.detail}")
        raise he
    except Exception as e:
        print(f"Unexpected error in deeper_insights_csv: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during CSV insights analysis: {str(e)}")


@router.get("/chat-session/{session_id}")
async def get_chat_session(session_id: str):
    """
    Get chat session history with context information
    """
    if session_id in chat_sessions:
        # Prepare response without raw DataFrames to avoid serialization issues
        session_history = []
        for entry in chat_sessions[session_id]:
            cleaned_entry = entry.copy()
            # Remove raw DataFrames from response to avoid serialization issues
            if 'dataframes_raw' in cleaned_entry:
                cleaned_entry['dataframes_raw'] = {
                    k: f"DataFrame with shape {v.shape}" for k, v in cleaned_entry['dataframes_raw'].items()
                }
            session_history.append(cleaned_entry)
        
        return {
            "sessionId": session_id,
            "history": session_history,
            "message_count": len(chat_sessions[session_id]),
            "has_data_context": any('dataframes_context' in entry for entry in chat_sessions[session_id])
        }
    else:
        raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/chat-session/{session_id}")
async def clear_chat_session(session_id: str):
    """
    Clear a specific chat session (useful for memory management)
    """
    if session_id in chat_sessions:
        del chat_sessions[session_id]
        return {"message": f"Session {session_id} cleared successfully"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")