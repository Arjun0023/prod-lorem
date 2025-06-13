from fastapi import APIRouter, Request, HTTPException
from utility.utils import NpEncoder, get_genai_client
from prompt.insights_prompt import INSIGHTS_PROMPT
import json

router = APIRouter()

@router.post("/deeper-insights")
async def deeper_insights(request: Request):
    try:
        # Parse the JSON body from the request
        data = await request.json()
        
        # Extract user question, context data, and language from the request
        user_question = data.get("question", "")
        context_data = data.get("context", {})
        language = data.get("language", "")
        
        # Validate required fields
        if not user_question:
            raise HTTPException(status_code=400, detail="Question is required")
        
        if not context_data:
            raise HTTPException(status_code=400, detail="No context data provided for insights analysis")
            
        # Convert context data to string representation for the model
        context_str = json.dumps(context_data, cls=NpEncoder, indent=2)
        
        # Get Gemini client
        model = get_genai_client()
        
        # Create a combined prompt for deeper insights
        prompt = INSIGHTS_PROMPT.format(
            question=user_question,
            language=language or "English",
            context=context_str
        )
        
        # Call the Gemini model with the insights prompt
        response = model.generate_content(prompt)
        
        # Return the insights
        return {
            "insights": response.text,
            "question": user_question,
            "language": language or "English"
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during insights analysis: {str(e)}")